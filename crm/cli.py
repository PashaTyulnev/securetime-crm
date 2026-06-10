from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy.exc import IntegrityError

from crm.campaign_sender import run_campaign_dry_run
from crm.config import DAILY_SEND_LIMIT, HOURLY_SEND_LIMIT
from crm.database import Base, engine, get_session
from crm.email_renderer import render_email as _render_email
from crm.importer import import_leads_from_csv
from crm.models import Campaign, EmailEvent, Lead, LeadStatus, Template

app = typer.Typer(
    name="crm",
    help="Outreach CRM — compliant B2B outreach toolkit.",
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)


# ---------------------------------------------------------------------------
# init-db
# ---------------------------------------------------------------------------

@app.command()
def init_db():
    """Create all database tables."""
    Base.metadata.create_all(engine)
    console.print("[green]Database initialized.[/green]")


# ---------------------------------------------------------------------------
# add-lead
# ---------------------------------------------------------------------------

@app.command()
def add_lead(
    company_name: str = typer.Option(..., "--company", "-c", prompt="Company name"),
    email: str = typer.Option(..., "--email", "-e", prompt="Email"),
    city: Optional[str] = typer.Option(None, "--city"),
    category: Optional[str] = typer.Option(None, "--category"),
    website: Optional[str] = typer.Option(None, "--website"),
    phone: Optional[str] = typer.Option(None, "--phone"),
    source_url: Optional[str] = typer.Option(None, "--source-url"),
    notes: Optional[str] = typer.Option(None, "--notes"),
):
    """Add a new lead."""
    session = get_session()
    lead = Lead(
        company_name=company_name,
        email=email or None,
        city=city,
        category=category,
        website=website,
        phone=phone,
        source_url=source_url,
        notes=notes,
        status=LeadStatus.new,
    )
    session.add(lead)
    session.commit()
    console.print(f"[green]Lead added:[/green] {company_name} (ID {lead.id})")


# ---------------------------------------------------------------------------
# list-leads
# ---------------------------------------------------------------------------

@app.command()
def list_leads(
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status"),
    category: Optional[str] = typer.Option(None, "--category", help="Filter by category"),
):
    """List leads, optionally filtered by status or category."""
    session = get_session()
    query = session.query(Lead)
    if status:
        try:
            query = query.filter(Lead.status == LeadStatus(status))
        except ValueError:
            valid = ", ".join(s.value for s in LeadStatus)
            err_console.print(f"[red]Invalid status '{status}'. Valid: {valid}[/red]")
            raise typer.Exit(1)
    if category:
        query = query.filter(Lead.category == category)

    leads = query.order_by(Lead.created_at.desc()).all()

    if not leads:
        console.print("No leads found.")
        return

    table = Table(title=f"Leads ({len(leads)})", show_lines=False)
    table.add_column("ID", style="cyan", justify="right")
    table.add_column("Company", style="bold")
    table.add_column("Email")
    table.add_column("City")
    table.add_column("Category")
    table.add_column("Status")
    table.add_column("Created")

    status_colors = {
        LeadStatus.new: "white",
        LeadStatus.verified: "bright_blue",
        LeadStatus.opted_out: "red",
        LeadStatus.drafted: "yellow",
        LeadStatus.sent: "green",
        LeadStatus.replied: "bright_green",
        LeadStatus.bounced: "orange3",
    }

    for lead in leads:
        color = status_colors.get(lead.status, "white")
        table.add_row(
            str(lead.id),
            lead.company_name,
            lead.email or "-",
            lead.city or "-",
            lead.category or "-",
            f"[{color}]{lead.status.value}[/{color}]",
            lead.created_at.strftime("%Y-%m-%d") if lead.created_at else "-",
        )

    console.print(table)


# ---------------------------------------------------------------------------
# add-template
# ---------------------------------------------------------------------------

@app.command()
def add_template(
    name: str = typer.Option(..., "--name", "-n", prompt="Template name"),
    subject: str = typer.Option(..., "--subject", "-s", prompt="Subject line (Jinja2)"),
    body_file: Optional[str] = typer.Option(None, "--body-file", "-f", help="Path to .j2 file"),
    body: Optional[str] = typer.Option(None, "--body", "-b", help="Inline Jinja2 body"),
):
    """Add an email template (body from file or inline string)."""
    if body_file:
        try:
            with open(body_file, encoding="utf-8") as fh:
                body_content = fh.read()
        except OSError as exc:
            err_console.print(f"[red]Cannot read file: {exc}[/red]")
            raise typer.Exit(1)
    elif body:
        body_content = body
    else:
        body_content = typer.prompt("Body (Jinja2 template)")

    session = get_session()
    template = Template(name=name, subject=subject, body=body_content)
    try:
        session.add(template)
        session.commit()
    except IntegrityError:
        err_console.print(f"[red]Template name '{name}' already exists.[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Template added:[/green] {name} (ID {template.id})")


# ---------------------------------------------------------------------------
# list-templates
# ---------------------------------------------------------------------------

@app.command()
def list_templates():
    """List all email templates."""
    session = get_session()
    templates = session.query(Template).order_by(Template.id).all()

    if not templates:
        console.print("No templates found.")
        return

    table = Table(title=f"Templates ({len(templates)})")
    table.add_column("ID", style="cyan", justify="right")
    table.add_column("Name", style="bold")
    table.add_column("Subject")
    table.add_column("Body preview")

    for t in templates:
        preview = t.body[:60].replace("\n", " ") + ("..." if len(t.body) > 60 else "")
        table.add_row(str(t.id), t.name, t.subject, preview)

    console.print(table)


# ---------------------------------------------------------------------------
# render-email
# ---------------------------------------------------------------------------

@app.command()
def render_email(
    lead_id: int = typer.Argument(..., help="Lead ID"),
    template_id: int = typer.Argument(..., help="Template ID"),
):
    """Render a template for a lead and print the result."""
    session = get_session()
    lead = session.get(Lead, lead_id)
    template = session.get(Template, template_id)

    if not lead:
        err_console.print(f"[red]Lead {lead_id} not found.[/red]")
        raise typer.Exit(1)
    if not template:
        err_console.print(f"[red]Template {template_id} not found.[/red]")
        raise typer.Exit(1)
    if lead.status == LeadStatus.opted_out:
        err_console.print(
            f"[red]Lead {lead_id} ({lead.company_name}) has opted out — skipping.[/red]"
        )
        raise typer.Exit(1)

    try:
        subject, body = _render_email(template, lead)
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    console.rule("[bold]Rendered Email[/bold]", characters="-")
    console.print(f"[bold]To:[/bold]       {lead.email or '(no email)'}")
    console.print(f"[bold]Company:[/bold]  {lead.company_name}")
    console.print(f"[bold]Subject:[/bold]  {subject}")
    console.rule(characters="-")
    console.print(body, markup=False)
    console.rule(characters="-")
    console.print(
        f"[dim]Rate limits: {DAILY_SEND_LIMIT}/day | {HOURLY_SEND_LIMIT}/hour[/dim]"
    )


# ---------------------------------------------------------------------------
# import-leads
# ---------------------------------------------------------------------------

@app.command()
def import_leads(
    csv_file: Path = typer.Argument(..., help="Path to CSV file"),
    encoding: str = typer.Option("utf-8-sig", "--encoding", help="CSV file encoding (default handles UTF-8 BOM from Excel/Windows)"),
):
    """Import leads from a CSV file with automatic deduplication.

    Expected columns: company_name (required), city, category, website,
    email, phone, source_url, notes.

    Deduplication order: email > website > company_name+city.
    """
    if not csv_file.exists():
        err_console.print(f"[red]File not found: {csv_file}[/red]")
        raise typer.Exit(1)

    session = get_session()
    try:
        result = import_leads_from_csv(session, csv_file, encoding=encoding)
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Imported:[/green]  {result.imported}")
    console.print(f"[yellow]Skipped:[/yellow]   {result.skipped}  (duplicates)")
    err_console.print(f"[red]Failed:[/red]    {result.failed}  (invalid rows)")

    if result.errors:
        console.print("\n[bold]Errors:[/bold]")
        for row_num, msg in result.errors:
            console.print(f"  row {row_num}: {msg}")


# ---------------------------------------------------------------------------
# send-campaign
# ---------------------------------------------------------------------------

@app.command()
def send_campaign(
    campaign_id: int = typer.Argument(..., help="Campaign ID"),
    dry_run: bool = typer.Option(
        True, "--dry-run/--no-dry-run",
        help="Preview without sending or changing lead status (default: on)",
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", "-l",
        help="Cap number of leads processed (stacks with rate limits)",
    ),
):
    """Dry-run a campaign: render emails for eligible leads without sending.

    Eligible leads: status=new with a non-null email address.
    Rate limits from config are always respected.
    Pass --no-dry-run once real sending is implemented.
    """
    if not dry_run:
        err_console.print("[red]Real sending is not implemented yet.[/red]")
        raise typer.Exit(1)

    session = get_session()
    try:
        previews, summary = run_campaign_dry_run(
            session,
            campaign_id,
            daily_limit=DAILY_SEND_LIMIT,
            hourly_limit=HOURLY_SEND_LIMIT,
            cap=limit,
        )
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    console.print(
        f"[bold][DRY RUN][/bold] Campaign {campaign_id} — "
        f"{summary.total_eligible} eligible lead(s)"
    )
    console.print(
        f"[dim]Rate limits: {summary.daily_remaining} daily remaining | "
        f"{summary.hourly_remaining} hourly remaining[/dim]"
    )

    if not previews:
        if summary.total_eligible > 0:
            console.print("[yellow]No slots available — rate limit reached for today/this hour.[/yellow]")
        else:
            console.print("[yellow]No eligible leads (status=new with email).[/yellow]")
        return

    console.rule(characters="-")
    for preview in previews:
        console.print(f"[cyan]Lead {preview.lead_id}[/cyan]  {preview.email}")
        console.print(f"  Subject: {preview.subject}", markup=False)
        console.print(f"  Body:    {preview.body_preview}", markup=False)
        console.rule(characters="-")

    console.print(f"[green]Would send to {summary.previewed} lead(s).[/green]")


# ---------------------------------------------------------------------------
# scrape-meinestadt
# ---------------------------------------------------------------------------

@app.command()
def scrape_meinestadt(
    cities: str = typer.Option(
        "dresden,freital,radebeul,pirna,meissen",
        "--cities",
        help="Comma-separated city slugs",
    ),
    limit_per_city: Optional[int] = typer.Option(
        None, "--limit-per-city", "-l",
        help="Max leads per city (default: no limit)",
    ),
    delay: float = typer.Option(
        2.5, "--delay",
        help="Seconds between HTTP requests",
    ),
    keywords: str = typer.Option(
        "security,sicherheit,secur,wach",
        "--keywords",
        help="Comma-separated name filter keywords (empty string = no filter)",
    ),
):
    """Scrape security-service leads from meinestadt.de Branchenbuch."""
    from crm.models import JobStatus, ScrapeJob
    from crm.scraper_meinestadt import scrape_meinestadt_security

    city_list = [c.strip() for c in cities.split(",") if c.strip()]
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()] if keywords else []
    session = get_session()

    job = ScrapeJob(
        source="meinestadt",
        cities_json=city_list,
        status=JobStatus.pending,
        total_found=0, total_imported=0, total_skipped=0, total_failed=0,
        log_text="",
    )
    session.add(job)
    session.commit()

    console.print(f"[bold]Scrape job #{job.id}[/bold]  cities: {', '.join(city_list)}")
    if limit_per_city:
        console.print(f"Limit per city: {limit_per_city}")
    console.print(f"Keywords: {', '.join(kw_list) if kw_list else '(none — all companies)'}")
    console.print(f"Delay: {delay}s between requests")
    console.rule(characters="-")

    try:
        scrape_meinestadt_security(
            city_list, session, job,
            limit_per_city=limit_per_city,
            delay=delay,
            name_keywords=kw_list if kw_list else None,
        )
    except Exception as exc:
        err_console.print(f"[red]Scrape failed: {exc}[/red]")
        raise typer.Exit(1)

    console.rule(characters="-")
    console.print(f"[green]Done![/green] Job #{job.id}  status={job.status.value}")
    console.print(f"  Found:     {job.total_found}")
    console.print(f"  Imported:  {job.total_imported}")
    console.print(f"  Skipped:   {job.total_skipped}")
    console.print(f"  Failed:    {job.total_failed}")
    console.rule(characters="-")
    console.print(job.log_text or "(empty log)", markup=False)


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------

@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
    port: int = typer.Option(8000, "--port", help="Bind port"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload (development)"),
):
    """Start the web UI."""
    import uvicorn
    uvicorn.run("web.app:app", host=host, port=port, reload=reload)
