"""Tests for crm/campaign_sender.py."""
from datetime import datetime, timedelta

import pytest

from crm.campaign_sender import BODY_PREVIEW_LEN, run_campaign_dry_run
from crm.models import Campaign, EmailEvent, Lead, LeadStatus, Template


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def template(session):
    tpl = Template(
        name="test_tpl",
        subject="Hello {{ company_name }}",
        body="Hi {{ company_name }}, this is an outreach message.",
    )
    session.add(tpl)
    session.flush()
    return tpl


@pytest.fixture
def campaign(session, template):
    camp = Campaign(name="test_campaign", template_id=template.id)
    session.add(camp)
    session.commit()
    return camp


def make_lead(session, *, company="Test Corp", email="test@corp.com", status=LeadStatus.new):
    lead = Lead(company_name=company, email=email, status=status)
    session.add(lead)
    session.flush()
    return lead


def make_sent_event(session, *, lead_id, campaign_id, created_at=None):
    event = EmailEvent(
        lead_id=lead_id,
        campaign_id=campaign_id,
        event_type="sent",
        created_at=created_at or datetime.utcnow(),
    )
    session.add(event)
    session.flush()
    return event


# Convenience wrapper with generous limits so most tests aren't about limits.
def dry_run(session, campaign_id, *, daily=100, hourly=100, cap=None):
    return run_campaign_dry_run(
        session, campaign_id,
        daily_limit=daily,
        hourly_limit=hourly,
        cap=cap,
    )


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestCampaignNotFound:
    def test_raises_value_error(self, session):
        with pytest.raises(ValueError, match="Campaign 999 not found"):
            dry_run(session, 999)


# ---------------------------------------------------------------------------
# Lead eligibility
# ---------------------------------------------------------------------------

class TestEligibility:
    def test_no_leads_returns_empty(self, session, campaign):
        previews, summary = dry_run(session, campaign.id)
        assert previews == []
        assert summary.total_eligible == 0

    def test_eligible_lead_returned(self, session, campaign):
        make_lead(session)
        session.commit()
        previews, summary = dry_run(session, campaign.id)
        assert len(previews) == 1
        assert summary.total_eligible == 1

    def test_skips_lead_without_email(self, session, campaign):
        make_lead(session, email=None)
        session.commit()
        previews, summary = dry_run(session, campaign.id)
        assert previews == []
        assert summary.total_eligible == 0

    def test_skips_lead_with_empty_email(self, session, campaign):
        make_lead(session, email="")
        session.commit()
        previews, summary = dry_run(session, campaign.id)
        assert previews == []
        assert summary.total_eligible == 0

    def test_skips_opted_out(self, session, campaign):
        make_lead(session, status=LeadStatus.opted_out)
        session.commit()
        previews, _ = dry_run(session, campaign.id)
        assert previews == []

    @pytest.mark.parametrize("status", [
        LeadStatus.verified,
        LeadStatus.drafted,
        LeadStatus.sent,
        LeadStatus.replied,
        LeadStatus.bounced,
    ])
    def test_skips_non_new_statuses(self, session, campaign, status):
        make_lead(session, status=status)
        session.commit()
        previews, _ = dry_run(session, campaign.id)
        assert previews == []

    def test_multiple_eligible_leads(self, session, campaign):
        for i in range(3):
            make_lead(session, company=f"Corp {i}", email=f"corp{i}@example.com")
        session.commit()
        previews, summary = dry_run(session, campaign.id)
        assert len(previews) == 3
        assert summary.total_eligible == 3


# ---------------------------------------------------------------------------
# Rendered content
# ---------------------------------------------------------------------------

class TestRenderedContent:
    def test_subject_is_rendered(self, session, campaign):
        make_lead(session, company="Acme Ltd")
        session.commit()
        previews, _ = dry_run(session, campaign.id)
        assert previews[0].subject == "Hello Acme Ltd"

    def test_body_preview_contains_company_name(self, session, campaign):
        make_lead(session, company="Acme Ltd")
        session.commit()
        previews, _ = dry_run(session, campaign.id)
        assert "Acme Ltd" in previews[0].body_preview

    def test_email_field_matches_lead(self, session, campaign):
        make_lead(session, email="contact@acme.com")
        session.commit()
        previews, _ = dry_run(session, campaign.id)
        assert previews[0].email == "contact@acme.com"

    def test_lead_id_matches(self, session, campaign):
        lead = make_lead(session)
        session.commit()
        previews, _ = dry_run(session, campaign.id)
        assert previews[0].lead_id == lead.id

    def test_body_preview_truncated_to_200(self, session, template, campaign):
        # Replace template body with something very long
        template.body = "A" * 500
        session.commit()
        make_lead(session)
        session.commit()
        previews, _ = dry_run(session, campaign.id)
        assert len(previews[0].body_preview) == BODY_PREVIEW_LEN

    def test_body_preview_not_padded_when_short(self, session, campaign):
        make_lead(session)
        session.commit()
        previews, _ = dry_run(session, campaign.id)
        # Default template body is short; preview should equal full body
        assert len(previews[0].body_preview) < BODY_PREVIEW_LEN

    def test_leads_ordered_by_id(self, session, campaign):
        for i in range(3):
            make_lead(session, company=f"Corp {i}", email=f"corp{i}@example.com")
        session.commit()
        previews, _ = dry_run(session, campaign.id)
        ids = [p.lead_id for p in previews]
        assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# Rate limits and --limit cap
# ---------------------------------------------------------------------------

class TestLimits:
    def test_cap_restricts_previews(self, session, campaign):
        for i in range(5):
            make_lead(session, company=f"Corp {i}", email=f"c{i}@example.com")
        session.commit()
        previews, summary = dry_run(session, campaign.id, cap=2)
        assert len(previews) == 2
        assert summary.previewed == 2
        assert summary.total_eligible == 5  # still shows full eligible count

    def test_daily_limit_restricts_previews(self, session, campaign):
        for i in range(5):
            make_lead(session, company=f"Corp {i}", email=f"c{i}@example.com")
        session.commit()
        previews, summary = dry_run(session, campaign.id, daily=2, hourly=100)
        assert len(previews) == 2
        assert summary.daily_remaining == 2

    def test_hourly_limit_restricts_previews(self, session, campaign):
        for i in range(5):
            make_lead(session, company=f"Corp {i}", email=f"c{i}@example.com")
        session.commit()
        previews, summary = dry_run(session, campaign.id, daily=100, hourly=2)
        assert len(previews) == 2
        assert summary.hourly_remaining == 2

    def test_most_restrictive_wins(self, session, campaign):
        for i in range(10):
            make_lead(session, company=f"Corp {i}", email=f"c{i}@example.com")
        session.commit()
        # daily=5, hourly=2, cap=8 → effective=2
        previews, _ = dry_run(session, campaign.id, daily=5, hourly=2, cap=8)
        assert len(previews) == 2

    def test_zero_daily_remaining_returns_empty(self, session, campaign):
        make_lead(session)
        session.commit()
        previews, summary = dry_run(session, campaign.id, daily=0)
        assert previews == []
        assert summary.daily_remaining == 0
        assert summary.total_eligible == 1  # lead exists but can't be sent

    def test_zero_hourly_remaining_returns_empty(self, session, campaign):
        make_lead(session)
        session.commit()
        previews, summary = dry_run(session, campaign.id, hourly=0)
        assert previews == []
        assert summary.hourly_remaining == 0

    def test_sent_events_today_reduce_daily_slots(self, session, campaign):
        lead = make_lead(session)
        session.commit()
        # Record 2 already-sent events today
        for _ in range(2):
            make_sent_event(session, lead_id=lead.id, campaign_id=campaign.id,
                            created_at=datetime.utcnow())
        session.commit()
        _, summary = dry_run(session, campaign.id, daily=3, hourly=100)
        assert summary.daily_remaining == 1

    def test_sent_events_this_hour_reduce_hourly_slots(self, session, campaign):
        lead = make_lead(session)
        session.commit()
        make_sent_event(session, lead_id=lead.id, campaign_id=campaign.id,
                        created_at=datetime.utcnow() - timedelta(minutes=30))
        session.commit()
        _, summary = dry_run(session, campaign.id, daily=100, hourly=2)
        assert summary.hourly_remaining == 1

    def test_old_sent_events_dont_reduce_hourly_slots(self, session, campaign):
        lead = make_lead(session)
        session.commit()
        # Event from 2 hours ago — outside the 1-hour window
        make_sent_event(session, lead_id=lead.id, campaign_id=campaign.id,
                        created_at=datetime.utcnow() - timedelta(hours=2))
        session.commit()
        _, summary = dry_run(session, campaign.id, daily=100, hourly=5)
        assert summary.hourly_remaining == 5

    def test_yesterday_sent_events_dont_reduce_daily_slots(self, session, campaign):
        lead = make_lead(session)
        session.commit()
        make_sent_event(session, lead_id=lead.id, campaign_id=campaign.id,
                        created_at=datetime.utcnow() - timedelta(days=1))
        session.commit()
        _, summary = dry_run(session, campaign.id, daily=3, hourly=100)
        assert summary.daily_remaining == 3
