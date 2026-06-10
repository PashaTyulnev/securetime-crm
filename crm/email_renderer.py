from typing import Callable, Optional

from jinja2 import BaseLoader, Environment, StrictUndefined, TemplateError

from crm.config import SENDER_EMAIL, SENDER_NAME
from crm.models import Lead, Template


def build_context(lead: Lead, track_fn: Optional[Callable[[str], str]] = None) -> dict:
    return {
        "company_name": lead.company_name or "",
        "city": lead.city or "",
        "category": lead.category or "",
        "website": lead.website or "",
        "email": lead.email or "",
        "phone": lead.phone or "",
        "sender_name": SENDER_NAME,
        "sender_email": SENDER_EMAIL,
        "lead": lead,
        # track(url) → CRM tracking URL; passthrough when no session provided (preview)
        "track": track_fn if track_fn is not None else (lambda url: url),
    }


def render_email(
    template: Template,
    lead: Lead,
    *,
    track_fn: Optional[Callable[[str], str]] = None,
) -> tuple[str, str]:
    env = Environment(loader=BaseLoader(), undefined=StrictUndefined)
    context = build_context(lead, track_fn)
    try:
        subject = env.from_string(template.subject).render(**context)
        body = env.from_string(template.body).render(**context)
    except TemplateError as exc:
        raise ValueError(f"Template render error: {exc}") from exc
    return subject, body
