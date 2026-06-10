from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from crm.campaign_sender import run_campaign_dry_run
from crm.config import DAILY_SEND_LIMIT, HOURLY_SEND_LIMIT
from crm.database import Base, engine, get_session
from crm.email_renderer import render_email
from crm.models import Campaign, EmailEvent, JobStatus, Lead, LeadStatus, ScrapeJob, Template, TrackingLink
from crm.scraper_meinestadt import scrape_meinestadt_security
from crm.tracking import make_track_fn

BASE_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="Outreach CRM", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
tmpl = Jinja2Templates(directory=BASE_DIR / "templates")


# ---------------------------------------------------------------------------
# DB dependency — overridable in tests
# ---------------------------------------------------------------------------

def db_session():
    session = get_session()
    try:
        yield session
    finally:
        session.close()


def _page_range(current: int, total: int) -> list:
    """Page numbers to show; None means ellipsis."""
    if total <= 7:
        return list(range(1, total + 1))
    pages: set = {1, total, current}
    for d in (-2, -1, 0, 1, 2):
        p = current + d
        if 1 <= p <= total:
            pages.add(p)
    result = []
    prev = None
    for p in sorted(pages):
        if prev is not None and p - prev > 1:
            result.append(None)
        result.append(p)
        prev = p
    return result


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(db_session)):
    total_leads = session.query(func.count(Lead.id)).scalar() or 0
    with_email = (
        session.query(func.count(Lead.id))
        .filter(Lead.email.isnot(None), Lead.email != "")
        .scalar() or 0
    )
    sent_count = (
        session.query(func.count(EmailEvent.id))
        .filter(EmailEvent.event_type == "sent")
        .scalar() or 0
    )
    opted_out = (
        session.query(func.count(Lead.id))
        .filter(Lead.status == LeadStatus.opted_out)
        .scalar() or 0
    )
    return tmpl.TemplateResponse(request, "dashboard.html", {
        "total_leads": total_leads,
        "with_email": with_email,
        "sent_count": sent_count,
        "opted_out": opted_out,
    })


# ---------------------------------------------------------------------------
# Leads list
# ---------------------------------------------------------------------------

@app.get("/leads", response_class=HTMLResponse)
def leads_list(
    request: Request,
    status: str | None = None,
    page: int = 1,
    session: Session = Depends(db_session),
):
    query = session.query(Lead)
    if status:
        try:
            query = query.filter(Lead.status == LeadStatus(status))
        except ValueError:
            status = None
    total = query.count()
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = max(1, min(page, total_pages))
    leads = query.order_by(Lead.id.desc()).offset((page - 1) * PER_PAGE).limit(PER_PAGE).all()
    return tmpl.TemplateResponse(request, "leads.html", {
        "leads": leads,
        "statuses": [s.value for s in LeadStatus],
        "current_status": status,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "page_range": _page_range(page, total_pages),
    })


# ---------------------------------------------------------------------------
# New lead
# ---------------------------------------------------------------------------

@app.get("/leads/new", response_class=HTMLResponse)
def new_lead_form(request: Request):
    return tmpl.TemplateResponse(request, "lead_form.html", {
        "lead": None,
        "statuses": [s.value for s in LeadStatus],
    })


@app.post("/leads/new")
async def create_lead(
    request: Request,
    company_name: str = Form(...),
    email: str = Form(""),
    city: str = Form(""),
    category: str = Form(""),
    website: str = Form(""),
    phone: str = Form(""),
    source_url: str = Form(""),
    notes: str = Form(""),
    session: Session = Depends(db_session),
):
    lead = Lead(
        company_name=company_name,
        email=email.strip() or None,
        city=city.strip() or None,
        category=category.strip() or None,
        website=website.strip() or None,
        phone=phone.strip() or None,
        source_url=source_url.strip() or None,
        notes=notes.strip() or None,
        status=LeadStatus.new,
    )
    session.add(lead)
    session.commit()
    return RedirectResponse(url=f"/leads/{lead.id}", status_code=303)


# ---------------------------------------------------------------------------
# Bulk delete leads
# ---------------------------------------------------------------------------

@app.post("/leads/bulk-delete")
async def bulk_delete_leads(
    request: Request,
    session: Session = Depends(db_session),
):
    form = await request.form()
    raw_ids = form.getlist("ids")
    ids = [int(i) for i in raw_ids if i]
    if ids:
        session.query(EmailEvent).filter(EmailEvent.lead_id.in_(ids)).delete(synchronize_session=False)
        session.query(Lead).filter(Lead.id.in_(ids)).delete(synchronize_session=False)
        session.commit()
    status_filter = form.get("status_filter", "")
    redirect_url = "/leads" + (f"?status={status_filter}" if status_filter else "")
    return RedirectResponse(url=redirect_url, status_code=303)


# ---------------------------------------------------------------------------
# Edit lead
# ---------------------------------------------------------------------------

@app.get("/leads/{lead_id}/edit", response_class=HTMLResponse)
def edit_lead_form(
    lead_id: int,
    request: Request,
    session: Session = Depends(db_session),
):
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return tmpl.TemplateResponse(request, "lead_form.html", {
        "lead": lead,
        "statuses": [s.value for s in LeadStatus],
    })


@app.post("/leads/{lead_id}/edit")
async def update_lead(
    lead_id: int,
    request: Request,
    company_name: str = Form(...),
    email: str = Form(""),
    city: str = Form(""),
    category: str = Form(""),
    website: str = Form(""),
    phone: str = Form(""),
    source_url: str = Form(""),
    notes: str = Form(""),
    status: str = Form("new"),
    session: Session = Depends(db_session),
):
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead.company_name = company_name
    lead.email = email.strip() or None
    lead.city = city.strip() or None
    lead.category = category.strip() or None
    lead.website = website.strip() or None
    lead.phone = phone.strip() or None
    lead.source_url = source_url.strip() or None
    lead.notes = notes.strip() or None
    try:
        lead.status = LeadStatus(status)
    except ValueError:
        pass
    session.commit()
    return RedirectResponse(url=f"/leads/{lead_id}", status_code=303)


# ---------------------------------------------------------------------------
# Lead detail
# ---------------------------------------------------------------------------

@app.get("/leads/{lead_id}", response_class=HTMLResponse)
def lead_detail(
    lead_id: int,
    request: Request,
    session: Session = Depends(db_session),
):
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    events = (
        session.query(EmailEvent)
        .filter(EmailEvent.lead_id == lead_id)
        .order_by(EmailEvent.created_at.desc())
        .all()
    )
    templates_list = session.query(Template).order_by(Template.id).all()
    tracking_links = (
        session.query(TrackingLink)
        .filter(TrackingLink.lead_id == lead_id)
        .order_by(TrackingLink.created_at.desc())
        .all()
    )
    return tmpl.TemplateResponse(request, "lead_detail.html", {
        "lead": lead,
        "events": events,
        "templates_list": templates_list,
        "tracking_links": tracking_links,
    })


# ---------------------------------------------------------------------------
# Delete lead
# ---------------------------------------------------------------------------

@app.post("/leads/{lead_id}/delete")
def delete_lead(
    lead_id: int,
    request: Request,
    session: Session = Depends(db_session),
):
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    session.query(EmailEvent).filter(EmailEvent.lead_id == lead_id).delete()
    session.delete(lead)
    session.commit()
    return RedirectResponse(url="/leads", status_code=303)


# ---------------------------------------------------------------------------
# Email preview
# ---------------------------------------------------------------------------

@app.get("/leads/{lead_id}/preview/{template_id}", response_class=HTMLResponse)
def preview_email(
    lead_id: int,
    template_id: int,
    request: Request,
    session: Session = Depends(db_session),
):
    lead = session.get(Lead, lead_id)
    template = session.get(Template, template_id)
    if lead is None or template is None:
        raise HTTPException(status_code=404)
    try:
        subject, body = render_email(template, lead)  # preview: track() is passthrough
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return tmpl.TemplateResponse(request, "preview.html", {
        "lead": lead,
        "template": template,
        "subject": subject,
        "body": body,
    })


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

@app.get("/templates", response_class=HTMLResponse)
def templates_page(request: Request, session: Session = Depends(db_session)):
    templates_list = session.query(Template).order_by(Template.id).all()
    return tmpl.TemplateResponse(request, "templates_page.html", {
        "templates_list": templates_list,
        "error": None,
    })


@app.post("/templates", response_class=HTMLResponse)
async def create_template(
    request: Request,
    name: str = Form(...),
    subject: str = Form(...),
    body: str = Form(...),
    session: Session = Depends(db_session),
):
    new_tpl = Template(name=name, subject=subject, body=body)
    try:
        session.add(new_tpl)
        session.commit()
    except IntegrityError:
        session.rollback()
        templates_list = session.query(Template).order_by(Template.id).all()
        return tmpl.TemplateResponse(request, "templates_page.html", {
            "templates_list": templates_list,
            "error": f"Template name '{name}' already exists.",
        }, status_code=422)
    return RedirectResponse(url="/templates", status_code=303)


# ---------------------------------------------------------------------------
# Campaign dry-run
# ---------------------------------------------------------------------------

@app.get("/campaigns/{campaign_id}/dry-run", response_class=HTMLResponse)
def campaign_dryrun(
    campaign_id: int,
    request: Request,
    session: Session = Depends(db_session),
):
    try:
        previews, summary = run_campaign_dry_run(
            session,
            campaign_id,
            daily_limit=DAILY_SEND_LIMIT,
            hourly_limit=HOURLY_SEND_LIMIT,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    campaign = session.get(Campaign, campaign_id)
    return tmpl.TemplateResponse(request, "campaign_dryrun.html", {
        "campaign": campaign,
        "previews": previews,
        "summary": summary,
    })


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

PER_PAGE = 50
DEFAULT_CITIES = "dresden,freital,radebeul,pirna,meissen"


# ---------------------------------------------------------------------------
# Click tracking
# ---------------------------------------------------------------------------

@app.get("/t/{token}")
def track_click(token: str, session: Session = Depends(db_session)):
    link = session.query(TrackingLink).filter(TrackingLink.token == token).first()
    if link is None:
        raise HTTPException(status_code=404, detail="Tracking link not found")
    if link.clicked_at is None:
        link.clicked_at = datetime.utcnow()
        if link.campaign_id is not None:
            session.add(EmailEvent(
                lead_id=link.lead_id,
                campaign_id=link.campaign_id,
                event_type="click",
                event_data={"url": link.destination_url},
            ))
        session.commit()
    return RedirectResponse(url=link.destination_url, status_code=302)

@app.get("/scraper", response_class=HTMLResponse)
def scraper_page(request: Request, session: Session = Depends(db_session)):
    jobs = session.query(ScrapeJob).order_by(ScrapeJob.id.desc()).limit(20).all()
    return tmpl.TemplateResponse(request, "scraper.html", {
        "jobs": jobs,
        "default_cities": DEFAULT_CITIES,
    })


DEFAULT_KEYWORDS = "security,sicherheit,secur,wach"

@app.post("/scraper/start")
async def start_scrape(
    request: Request,
    cities: str = Form(...),
    limit_per_city: int = Form(20),
    keywords: str = Form(DEFAULT_KEYWORDS),
    session: Session = Depends(db_session),
):
    city_list = [c.strip() for c in cities.split(",") if c.strip()]
    if not city_list:
        raise HTTPException(status_code=422, detail="At least one city required")
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()] if keywords.strip() else []

    job = ScrapeJob(
        source="meinestadt",
        cities_json=city_list,
        status=JobStatus.pending,
        total_found=0,
        total_imported=0,
        total_skipped=0,
        total_failed=0,
        log_text="",
    )
    session.add(job)
    session.commit()
    job_id = job.id
    lpc = limit_per_city if limit_per_city > 0 else None

    def _run_scrape():
        # Background thread gets its own DB session.
        from crm.database import get_session as _get_session
        thread_session = _get_session()
        try:
            thread_job = thread_session.get(ScrapeJob, job_id)
            scrape_meinestadt_security(
                city_list, thread_session, thread_job,
                limit_per_city=lpc,
                name_keywords=kw_list if kw_list else None,
            )
        except Exception:
            pass
        finally:
            thread_session.close()

    threading.Thread(target=_run_scrape, daemon=True).start()

    # Redirect immediately — job detail page auto-refreshes every 3 s
    return RedirectResponse(url=f"/scraper/jobs/{job_id}", status_code=303)


@app.get("/scraper/jobs/{job_id}", response_class=HTMLResponse)
def scraper_job_detail(
    job_id: int,
    request: Request,
    session: Session = Depends(db_session),
):
    job = session.get(ScrapeJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return tmpl.TemplateResponse(request, "scraper_job.html", {"job": job})
