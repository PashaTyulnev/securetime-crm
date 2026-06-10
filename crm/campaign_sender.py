from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from crm.email_renderer import render_email
from crm.models import Campaign, EmailEvent, Lead, LeadStatus

BODY_PREVIEW_LEN = 200


@dataclass
class CampaignPreview:
    lead_id: int
    email: str
    subject: str
    body_preview: str


@dataclass
class DryRunSummary:
    total_eligible: int   # leads with status=new and non-null email, before any cap
    previewed: int        # actually returned after rate limits and --limit cap
    daily_remaining: int  # slots left in the daily window
    hourly_remaining: int # slots left in the hourly window


def _global_sent_count(session: Session, since: datetime) -> int:
    """Count all 'sent' email events across all campaigns created on or after `since`."""
    return (
        session.query(func.count(EmailEvent.id))
        .filter(
            EmailEvent.event_type == "sent",
            EmailEvent.created_at >= since,
        )
        .scalar()
        or 0
    )


def run_campaign_dry_run(
    session: Session,
    campaign_id: int,
    *,
    daily_limit: int,
    hourly_limit: int,
    cap: int | None = None,
) -> tuple[list[CampaignPreview], DryRunSummary]:
    """Render the campaign template for each eligible lead without persisting anything.

    Eligibility: status=new, email not null/empty.
    Effective slot count = min(daily_remaining, hourly_remaining[, cap]).
    """
    campaign = session.get(Campaign, campaign_id)
    if campaign is None:
        raise ValueError(f"Campaign {campaign_id} not found")

    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    hour_ago = now - timedelta(hours=1)

    sent_today = _global_sent_count(session, today_start)
    sent_this_hour = _global_sent_count(session, hour_ago)

    daily_remaining = max(0, daily_limit - sent_today)
    hourly_remaining = max(0, hourly_limit - sent_this_hour)
    effective_limit = min(daily_remaining, hourly_remaining)
    if cap is not None:
        effective_limit = min(effective_limit, cap)

    candidates = (
        session.query(Lead)
        .filter(
            Lead.status == LeadStatus.new,
            Lead.email.isnot(None),
            Lead.email != "",
        )
        .order_by(Lead.id)
        .all()
    )

    previews: list[CampaignPreview] = []
    for lead in candidates:
        if len(previews) >= effective_limit:
            break
        subject, body = render_email(campaign.template, lead)
        previews.append(CampaignPreview(
            lead_id=lead.id,
            email=lead.email,
            subject=subject,
            body_preview=body[:BODY_PREVIEW_LEN],
        ))

    return previews, DryRunSummary(
        total_eligible=len(candidates),
        previewed=len(previews),
        daily_remaining=daily_remaining,
        hourly_remaining=hourly_remaining,
    )
