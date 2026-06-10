"""Tracking link generation."""
from __future__ import annotations

import uuid
from typing import Callable, Optional

from sqlalchemy.orm import Session

from crm.config import BASE_URL
from crm.models import TrackingLink


def make_tracking_url(
    session: Session,
    lead_id: int,
    destination_url: str,
    campaign_id: Optional[int] = None,
) -> str:
    """Persist a TrackingLink and return the CRM click-tracking URL."""
    token = uuid.uuid4().hex
    link = TrackingLink(
        token=token,
        lead_id=lead_id,
        campaign_id=campaign_id,
        destination_url=destination_url,
    )
    session.add(link)
    session.flush()
    return f"{BASE_URL}/t/{token}"


def make_track_fn(
    session: Session,
    lead_id: int,
    campaign_id: Optional[int] = None,
) -> Callable[[str], str]:
    """Return a track(url) callable for use inside Jinja2 email templates."""
    def track(destination_url: str) -> str:
        return make_tracking_url(session, lead_id, destination_url, campaign_id)
    return track
