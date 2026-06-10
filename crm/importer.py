from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from crm.models import Lead, LeadStatus

EXPECTED_COLUMNS = {
    "company_name", "city", "category", "website",
    "email", "phone", "source_url", "notes",
}


@dataclass
class ImportResult:
    imported: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[tuple[int, str]] = field(default_factory=list)


def _normalize_website(url: str) -> str:
    """Strip protocol and trailing slash for stable website comparison."""
    url = url.strip().lower()
    url = re.sub(r"^https?://(www\.)?", "", url)
    return url.rstrip("/")


def _dedup_key(row: dict) -> tuple[str, str]:
    """Return the dedup (strategy, value) pair for a CSV row."""
    email = (row.get("email") or "").strip().lower()
    if email:
        return ("email", email)
    website = (row.get("website") or "").strip()
    if website:
        return ("website", _normalize_website(website))
    company = (row.get("company_name") or "").strip().lower()
    city = (row.get("city") or "").strip().lower()
    return ("name_city", f"{company}|{city}")


def _existing_keys(session: Session) -> set[tuple[str, str]]:
    """Build the full set of dedup keys from leads already in the DB."""
    keys: set[tuple[str, str]] = set()
    for lead in session.query(Lead).all():
        if lead.email:
            keys.add(("email", lead.email.strip().lower()))
        if lead.website:
            keys.add(("website", _normalize_website(lead.website)))
        company = (lead.company_name or "").strip().lower()
        city = (lead.city or "").strip().lower()
        keys.add(("name_city", f"{company}|{city}"))
    return keys


def import_leads_from_csv(
    session: Session,
    csv_path: Path | str,
    encoding: str = "utf-8-sig",
) -> ImportResult:
    result = ImportResult()
    db_keys = _existing_keys(session)
    seen_keys: set[tuple[str, str]] = set()

    try:
        fh = open(csv_path, encoding=encoding, newline="")
    except OSError as exc:
        raise ValueError(f"Cannot open CSV: {exc}") from exc

    with fh:
        # restval="" so missing columns default to empty string
        reader = csv.DictReader(fh, restval="")

        if not reader.fieldnames:
            return result

        for row_num, raw in enumerate(reader, start=2):
            row = {k.strip(): (v.strip() if v else "") for k, v in raw.items()}

            company_name = row.get("company_name", "").strip()
            if not company_name:
                result.failed += 1
                result.errors.append((row_num, "company_name is required"))
                continue

            key = _dedup_key(row)
            if key in db_keys or key in seen_keys:
                result.skipped += 1
                continue

            seen_keys.add(key)
            session.add(Lead(
                company_name=company_name,
                city=row.get("city") or None,
                category=row.get("category") or None,
                website=row.get("website") or None,
                email=row.get("email") or None,
                phone=row.get("phone") or None,
                source_url=row.get("source_url") or None,
                notes=row.get("notes") or None,
                status=LeadStatus.new,
            ))
            result.imported += 1

    session.commit()
    return result
