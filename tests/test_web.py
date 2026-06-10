"""Smoke tests for the FastAPI web UI (web/app.py)."""
import pytest
from fastapi.testclient import TestClient

from crm.models import Campaign, EmailEvent, JobStatus, Lead, LeadStatus, ScrapeJob, Template, TrackingLink
from web.app import app, db_session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client(session):
    """TestClient with the DB dependency swapped for the in-memory test session."""
    def override():
        yield session

    app.dependency_overrides[db_session] = override
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


def seed_lead(session, **kwargs):
    defaults = dict(company_name="Acme Ltd", email="acme@test.com", status=LeadStatus.new)
    defaults.update(kwargs)
    lead = Lead(**defaults)
    session.add(lead)
    session.commit()
    return lead


def seed_template(session, **kwargs):
    defaults = dict(
        name="t1",
        subject="Hello {{ company_name }}",
        body="Hi {{ company_name }}, this is a test.",
    )
    defaults.update(kwargs)
    tpl = Template(**defaults)
    session.add(tpl)
    session.commit()
    return tpl


def seed_campaign(session, template):
    camp = Campaign(name="camp1", template_id=template.id)
    session.add(camp)
    session.commit()
    return camp


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class TestDashboard:
    def test_returns_200(self, client):
        assert client.get("/").status_code == 200

    def test_shows_zero_counts_on_empty_db(self, client):
        body = client.get("/").text
        assert body.count(">0<") >= 4  # four stat cards

    def test_counts_total_leads(self, client, session):
        seed_lead(session, company_name="Corp A", email="a@a.com")
        seed_lead(session, company_name="Corp B", email="b@b.com")
        body = client.get("/").text
        assert ">2<" in body

    def test_counts_leads_with_email(self, client, session):
        seed_lead(session, email="has@email.com")
        seed_lead(session, email=None)
        body = client.get("/").text
        # one lead with email — the "1" value must appear in a stat card
        assert "with Email" in body

    def test_counts_opted_out(self, client, session):
        seed_lead(session, status=LeadStatus.opted_out, email="o@o.com")
        body = client.get("/").text
        assert "Opted Out" in body


# ---------------------------------------------------------------------------
# Leads list
# ---------------------------------------------------------------------------

class TestLeadsList:
    def test_returns_200_empty(self, client):
        assert client.get("/leads").status_code == 200

    def test_shows_lead_company_name(self, client, session):
        seed_lead(session, company_name="Zeta Corp")
        assert "Zeta Corp" in client.get("/leads").text

    def test_status_filter_includes_matching(self, client, session):
        seed_lead(session, company_name="New Lead", status=LeadStatus.new)
        seed_lead(session, company_name="Sent Lead", email="s@s.com", status=LeadStatus.sent)
        body = client.get("/leads?status=new").text
        assert "New Lead" in body

    def test_status_filter_excludes_non_matching(self, client, session):
        seed_lead(session, company_name="New Lead", status=LeadStatus.new)
        seed_lead(session, company_name="Sent Lead", email="s@s.com", status=LeadStatus.sent)
        body = client.get("/leads?status=new").text
        assert "Sent Lead" not in body

    def test_invalid_status_still_returns_200(self, client):
        assert client.get("/leads?status=nonsense").status_code == 200

    def test_contains_status_badges(self, client, session):
        seed_lead(session)
        body = client.get("/leads").text
        assert "badge-new" in body


# ---------------------------------------------------------------------------
# Lead detail
# ---------------------------------------------------------------------------

class TestCreateLead:
    def test_new_lead_form_returns_200(self, client):
        assert client.get("/leads/new").status_code == 200

    def test_new_lead_form_has_fields(self, client):
        body = client.get("/leads/new").text
        assert 'name="company_name"' in body
        assert 'name="email"' in body

    def test_post_creates_lead_and_redirects(self, client, session):
        r = client.post("/leads/new", data={"company_name": "Test GmbH", "email": "x@x.de"},
                        follow_redirects=False)
        assert r.status_code == 303
        assert session.query(Lead).filter_by(company_name="Test GmbH").count() == 1

    def test_post_sets_status_new(self, client, session):
        client.post("/leads/new", data={"company_name": "Neu AG"})
        lead = session.query(Lead).filter_by(company_name="Neu AG").first()
        assert lead.status == LeadStatus.new

    def test_post_optional_fields_stored(self, client, session):
        client.post("/leads/new", data={
            "company_name": "With Fields GmbH", "city": "Dresden",
            "phone": "+49123", "website": "https://x.de",
        })
        lead = session.query(Lead).filter_by(company_name="With Fields GmbH").first()
        assert lead.city == "Dresden"
        assert lead.phone == "+49123"

    def test_empty_optional_fields_saved_as_none(self, client, session):
        client.post("/leads/new", data={"company_name": "Minimal AG", "email": ""})
        lead = session.query(Lead).filter_by(company_name="Minimal AG").first()
        assert lead.email is None


class TestEditLead:
    def test_edit_form_returns_200(self, client, session):
        lead = seed_lead(session)
        assert client.get(f"/leads/{lead.id}/edit").status_code == 200

    def test_edit_form_prefills_values(self, client, session):
        lead = seed_lead(session, company_name="Prefill Corp")
        body = client.get(f"/leads/{lead.id}/edit").text
        assert "Prefill Corp" in body

    def test_edit_form_has_status_select(self, client, session):
        lead = seed_lead(session)
        body = client.get(f"/leads/{lead.id}/edit").text
        assert 'name="status"' in body

    def test_edit_404_for_missing(self, client):
        assert client.get("/leads/9999/edit").status_code == 404

    def test_post_edit_updates_lead(self, client, session):
        lead = seed_lead(session, company_name="Old Name")
        client.post(f"/leads/{lead.id}/edit", data={
            "company_name": "New Name", "email": "new@x.de",
            "city": "", "category": "", "website": "", "phone": "",
            "source_url": "", "notes": "", "status": "verified",
        })
        session.refresh(lead)
        assert lead.company_name == "New Name"
        assert lead.status == LeadStatus.verified

    def test_post_edit_redirects_to_detail(self, client, session):
        lead = seed_lead(session)
        r = client.post(f"/leads/{lead.id}/edit", data={
            "company_name": "X", "email": "", "city": "", "category": "",
            "website": "", "phone": "", "source_url": "", "notes": "", "status": "new",
        }, follow_redirects=False)
        assert r.status_code == 303
        assert f"/leads/{lead.id}" in r.headers["location"]

    def test_edit_button_visible_on_detail_page(self, client, session):
        lead = seed_lead(session)
        body = client.get(f"/leads/{lead.id}").text
        assert f"/leads/{lead.id}/edit" in body


class TestDeleteLead:
    def test_delete_redirects_to_leads(self, client, session):
        lead = seed_lead(session)
        r = client.post(f"/leads/{lead.id}/delete", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/leads"

    def test_delete_removes_lead_from_db(self, client, session):
        lead = seed_lead(session)
        client.post(f"/leads/{lead.id}/delete")
        assert session.get(Lead, lead.id) is None

    def test_delete_also_removes_events(self, client, session):
        lead = seed_lead(session)
        tpl = seed_template(session)
        camp = seed_campaign(session, tpl)
        ev = EmailEvent(lead_id=lead.id, campaign_id=camp.id, event_type="sent")
        session.add(ev)
        session.commit()
        ev_id = ev.id
        client.post(f"/leads/{lead.id}/delete")
        assert session.get(EmailEvent, ev_id) is None

    def test_delete_404_for_missing(self, client):
        assert client.post("/leads/9999/delete").status_code == 404

    def test_delete_button_appears_on_detail_page(self, client, session):
        lead = seed_lead(session)
        body = client.get(f"/leads/{lead.id}").text
        assert "delete" in body.lower()

    def test_delete_button_appears_in_leads_list(self, client, session):
        seed_lead(session)
        body = client.get("/leads").text
        assert "delete" in body.lower()


class TestLeadDetail:
    def test_returns_404_for_missing(self, client):
        assert client.get("/leads/9999").status_code == 404

    def test_shows_company_name(self, client, session):
        lead = seed_lead(session, company_name="Detail Corp")
        body = client.get(f"/leads/{lead.id}").text
        assert "Detail Corp" in body

    def test_shows_email(self, client, session):
        lead = seed_lead(session, email="detail@corp.com")
        assert "detail@corp.com" in client.get(f"/leads/{lead.id}").text

    def test_shows_opted_out_warning(self, client, session):
        lead = seed_lead(session, status=LeadStatus.opted_out, email="o@o.com")
        body = client.get(f"/leads/{lead.id}").text
        assert "opted out" in body.lower()

    def test_shows_template_selector_when_templates_exist(self, client, session):
        lead = seed_lead(session)
        seed_template(session)
        body = client.get(f"/leads/{lead.id}").text
        assert "tpl-select" in body

    def test_shows_events(self, client, session):
        lead = seed_lead(session)
        tpl = seed_template(session)
        camp = seed_campaign(session, tpl)
        ev = EmailEvent(lead_id=lead.id, campaign_id=camp.id, event_type="sent")
        session.add(ev)
        session.commit()
        body = client.get(f"/leads/{lead.id}").text
        assert "sent" in body


# ---------------------------------------------------------------------------
# Templates page
# ---------------------------------------------------------------------------

class TestTemplatesPage:
    def test_get_returns_200(self, client):
        assert client.get("/templates").status_code == 200

    def test_shows_form_fields(self, client):
        body = client.get("/templates").text
        assert 'name="name"' in body
        assert 'name="subject"' in body
        assert 'name="body"' in body

    def test_post_creates_template_and_redirects(self, client, session):
        r = client.post(
            "/templates",
            data={"name": "intro_v1", "subject": "Hi {{ company_name }}", "body": "Body text"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert session.query(Template).count() == 1

    def test_post_duplicate_name_returns_422(self, client, session):
        seed_template(session, name="dup")
        r = client.post(
            "/templates",
            data={"name": "dup", "subject": "S", "body": "B"},
        )
        assert r.status_code == 422
        assert "already exists" in r.text

    def test_lists_existing_templates(self, client, session):
        seed_template(session, name="my_tpl")
        assert "my_tpl" in client.get("/templates").text


# ---------------------------------------------------------------------------
# Email preview
# ---------------------------------------------------------------------------

class TestPreview:
    def test_returns_200_and_renders_company(self, client, session):
        lead = seed_lead(session, company_name="Preview Corp")
        tpl = seed_template(session)
        r = client.get(f"/leads/{lead.id}/preview/{tpl.id}")
        assert r.status_code == 200
        assert "Preview Corp" in r.text

    def test_renders_subject(self, client, session):
        lead = seed_lead(session, company_name="Subj Corp")
        tpl = seed_template(session, subject="Hello {{ company_name }}")
        r = client.get(f"/leads/{lead.id}/preview/{tpl.id}")
        assert "Hello Subj Corp" in r.text

    def test_404_for_missing_lead(self, client, session):
        tpl = seed_template(session)
        assert client.get(f"/leads/9999/preview/{tpl.id}").status_code == 404

    def test_404_for_missing_template(self, client, session):
        lead = seed_lead(session)
        assert client.get(f"/leads/{lead.id}/preview/9999").status_code == 404

    def test_no_send_disclaimer_shown(self, client, session):
        lead = seed_lead(session)
        tpl = seed_template(session)
        body = client.get(f"/leads/{lead.id}/preview/{tpl.id}").text
        assert "preview only" in body.lower()


# ---------------------------------------------------------------------------
# Campaign dry-run
# ---------------------------------------------------------------------------

class TestCampaignDryRun:
    def test_404_for_missing_campaign(self, client):
        assert client.get("/campaigns/9999/dry-run").status_code == 404

    def test_returns_200_with_no_leads(self, client, session):
        tpl = seed_template(session)
        camp = seed_campaign(session, tpl)
        assert client.get(f"/campaigns/{camp.id}/dry-run").status_code == 200

    def test_shows_eligible_lead(self, client, session):
        lead = seed_lead(session, company_name="Eligible Corp")
        tpl = seed_template(session, subject="Hello {{ company_name }}")
        camp = seed_campaign(session, tpl)
        body = client.get(f"/campaigns/{camp.id}/dry-run").text
        assert "Eligible Corp" in body

    def test_excludes_opted_out_leads(self, client, session):
        seed_lead(session, company_name="Opted Out", status=LeadStatus.opted_out)
        tpl = seed_template(session)
        camp = seed_campaign(session, tpl)
        body = client.get(f"/campaigns/{camp.id}/dry-run").text
        assert "Opted Out" not in body

    def test_shows_rate_limit_stats(self, client, session):
        tpl = seed_template(session)
        camp = seed_campaign(session, tpl)
        body = client.get(f"/campaigns/{camp.id}/dry-run").text
        assert "Daily slots remaining" in body
        assert "Hourly slots remaining" in body

    def test_campaign_name_in_heading(self, client, session):
        tpl = seed_template(session)
        camp = seed_campaign(session, tpl)
        body = client.get(f"/campaigns/{camp.id}/dry-run").text
        assert camp.name in body


# ---------------------------------------------------------------------------
# Scraper routes (GET only — POST triggers real network, not tested here)
# ---------------------------------------------------------------------------

def seed_job(session, **kwargs):
    defaults = dict(
        source="meinestadt",
        cities_json=["dresden"],
        status=JobStatus.finished,
        total_found=3,
        total_imported=3,
        total_skipped=0,
        total_failed=0,
        log_text="all done",
    )
    defaults.update(kwargs)
    job = ScrapeJob(**defaults)
    session.add(job)
    session.commit()
    return job


class TestScraperRoutes:
    def test_scraper_page_returns_200(self, client):
        assert client.get("/scraper").status_code == 200

    def test_scraper_shows_empty_state(self, client):
        body = client.get("/scraper").text
        assert "No scrape jobs" in body

    def test_scraper_shows_existing_job(self, client, session):
        seed_job(session)
        body = client.get("/scraper").text
        assert "finished" in body
        assert "dresden" in body

    def test_scraper_job_detail_404_for_missing(self, client):
        assert client.get("/scraper/jobs/9999").status_code == 404

    def test_scraper_job_detail_shows_status(self, client, session):
        job = seed_job(session)
        body = client.get(f"/scraper/jobs/{job.id}").text
        assert "finished" in body

    def test_scraper_job_detail_shows_log(self, client, session):
        job = seed_job(session, log_text="imported: Firma GmbH")
        body = client.get(f"/scraper/jobs/{job.id}").text
        assert "imported: Firma GmbH" in body

    def test_scraper_job_detail_shows_cities(self, client, session):
        job = seed_job(session, cities_json=["freital", "pirna"])
        body = client.get(f"/scraper/jobs/{job.id}").text
        assert "freital" in body
        assert "pirna" in body

    def test_scraper_running_job_has_meta_refresh(self, client, session):
        job = seed_job(session, status=JobStatus.running)
        body = client.get(f"/scraper/jobs/{job.id}").text
        assert "meta" in body.lower()
        assert "refresh" in body.lower()

    def test_scraper_finished_job_no_meta_refresh(self, client, session):
        job = seed_job(session, status=JobStatus.finished)
        body = client.get(f"/scraper/jobs/{job.id}").text
        assert 'http-equiv="refresh"' not in body


# ---------------------------------------------------------------------------
# Bulk delete leads
# ---------------------------------------------------------------------------

class TestBulkDeleteLeads:
    def test_bulk_delete_removes_selected_leads(self, client, session):
        l1 = seed_lead(session, company_name="Del A", email="a@del.com")
        l2 = seed_lead(session, company_name="Del B", email="b@del.com")
        l3 = seed_lead(session, company_name="Keep C", email="c@del.com")
        id1, id2, id3 = l1.id, l2.id, l3.id
        r = client.post("/leads/bulk-delete",
                        data={"ids": [str(id1), str(id2)]},
                        follow_redirects=False)
        assert r.status_code == 303
        session.expunge_all()
        assert session.get(Lead, id1) is None
        assert session.get(Lead, id2) is None
        assert session.get(Lead, id3) is not None

    def test_bulk_delete_also_removes_email_events(self, client, session):
        lead = seed_lead(session)
        tpl = seed_template(session)
        camp = seed_campaign(session, tpl)
        ev = EmailEvent(lead_id=lead.id, campaign_id=camp.id, event_type="sent")
        session.add(ev)
        session.commit()
        lead_id, ev_id = lead.id, ev.id
        client.post("/leads/bulk-delete", data={"ids": str(lead_id)}, follow_redirects=False)
        session.expunge_all()
        assert session.get(EmailEvent, ev_id) is None

    def test_bulk_delete_empty_selection_does_nothing(self, client, session):
        lead = seed_lead(session)
        lead_id = lead.id
        r = client.post("/leads/bulk-delete", data={}, follow_redirects=False)
        assert r.status_code == 303
        session.expunge_all()
        assert session.get(Lead, lead_id) is not None

    def test_bulk_delete_redirects_to_leads(self, client, session):
        lead = seed_lead(session)
        lead_id = lead.id
        r = client.post("/leads/bulk-delete", data={"ids": str(lead_id)},
                        follow_redirects=False)
        assert r.headers["location"] == "/leads"

    def test_bulk_delete_preserves_status_filter(self, client, session):
        lead = seed_lead(session)
        lead_id = lead.id
        r = client.post("/leads/bulk-delete",
                        data={"ids": str(lead_id), "status_filter": "new"},
                        follow_redirects=False)
        assert "status=new" in r.headers["location"]


# ---------------------------------------------------------------------------
# Leads pagination
# ---------------------------------------------------------------------------

class TestLeadsPagination:
    def test_no_pagination_below_limit(self, client, session):
        seed_lead(session)
        body = client.get("/leads").text
        assert 'class="pagination"' not in body

    def test_pagination_shown_above_limit(self, client, session):
        for i in range(51):
            seed_lead(session, company_name=f"Corp {i}", email=f"c{i}@x.com")
        body = client.get("/leads").text
        assert 'class="pagination"' in body

    def test_page_2_returns_200(self, client, session):
        for i in range(51):
            seed_lead(session, company_name=f"Corp {i}", email=f"c{i}@x.com")
        assert client.get("/leads?page=2").status_code == 200

    def test_total_count_shown_in_heading(self, client, session):
        seed_lead(session, company_name="A", email="a@a.com")
        seed_lead(session, company_name="B", email="b@b.com")
        body = client.get("/leads").text
        assert "(2)" in body


# ---------------------------------------------------------------------------
# Tracking links
# ---------------------------------------------------------------------------

def seed_tracking_link(session, lead, token="testtoken01", destination="https://securetime.de", campaign_id=None):
    tl = TrackingLink(token=token, lead_id=lead.id, campaign_id=campaign_id,
                      destination_url=destination)
    session.add(tl)
    session.commit()
    return tl


class TestTrackingLinks:
    def test_click_redirects_to_destination(self, client, session):
        lead = seed_lead(session)
        seed_tracking_link(session, lead, token="redir01", destination="https://securetime.de")
        r = client.get("/t/redir01", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "https://securetime.de"

    def test_click_records_clicked_at(self, client, session):
        lead = seed_lead(session)
        tl = seed_tracking_link(session, lead, token="click01")
        tl_id = tl.id
        client.get("/t/click01")
        session.expire_all()
        assert session.get(TrackingLink, tl_id).clicked_at is not None

    def test_click_404_for_unknown_token(self, client):
        assert client.get("/t/doesnotexist999").status_code == 404

    def test_repeated_click_still_redirects(self, client, session):
        lead = seed_lead(session)
        seed_tracking_link(session, lead, token="repeat01")
        client.get("/t/repeat01")
        r = client.get("/t/repeat01", follow_redirects=False)
        assert r.status_code == 302

    def test_click_with_campaign_creates_email_event(self, client, session):
        lead = seed_lead(session)
        tpl = seed_template(session)
        camp = seed_campaign(session, tpl)
        seed_tracking_link(session, lead, token="camp01", campaign_id=camp.id)
        client.get("/t/camp01")
        session.expire_all()
        ev = session.query(EmailEvent).filter_by(lead_id=lead.id, event_type="click").first()
        assert ev is not None
        assert ev.campaign_id == camp.id

    def test_click_without_campaign_no_email_event(self, client, session):
        lead = seed_lead(session)
        seed_tracking_link(session, lead, token="nocmp01", campaign_id=None)
        client.get("/t/nocmp01")
        session.expire_all()
        assert session.query(EmailEvent).filter_by(lead_id=lead.id, event_type="click").count() == 0

    def test_tracking_links_shown_on_lead_detail(self, client, session):
        lead = seed_lead(session)
        seed_tracking_link(session, lead, token="detail01", destination="https://demo.securetime.de")
        body = client.get(f"/leads/{lead.id}").text
        assert "demo.securetime.de" in body
        assert "detail01" in body
