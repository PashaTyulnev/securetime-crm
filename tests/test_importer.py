"""Tests for crm/importer.py."""
import csv
from pathlib import Path

import pytest

from crm.importer import _dedup_key, _normalize_website, import_leads_from_csv
from crm.models import Lead, LeadStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

HEADER = ["company_name", "city", "category", "website", "email", "phone", "source_url", "notes"]


def write_csv(path: Path, rows: list[dict], header: list[str] = HEADER) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


# ---------------------------------------------------------------------------
# Unit tests: helpers
# ---------------------------------------------------------------------------

class TestNormalizeWebsite:
    def test_strips_https(self):
        assert _normalize_website("https://acme.de") == "acme.de"

    def test_strips_http(self):
        assert _normalize_website("http://acme.de") == "acme.de"

    def test_strips_www(self):
        assert _normalize_website("https://www.acme.de") == "acme.de"

    def test_strips_trailing_slash(self):
        assert _normalize_website("https://acme.de/") == "acme.de"

    def test_lowercases(self):
        assert _normalize_website("HTTPS://Acme.DE") == "acme.de"

    def test_plain_domain_unchanged(self):
        assert _normalize_website("acme.de") == "acme.de"


class TestDedupKey:
    def test_email_takes_priority(self):
        row = {"email": "a@b.com", "website": "acme.de", "company_name": "Acme", "city": ""}
        assert _dedup_key(row) == ("email", "a@b.com")

    def test_website_when_no_email(self):
        row = {"email": "", "website": "https://acme.de", "company_name": "Acme", "city": ""}
        assert _dedup_key(row) == ("website", "acme.de")

    def test_name_city_when_neither(self):
        row = {"email": "", "website": "", "company_name": "Acme GmbH", "city": "Berlin"}
        assert _dedup_key(row) == ("name_city", "acme gmbh|berlin")

    def test_name_city_empty_city(self):
        row = {"email": "", "website": "", "company_name": "Acme", "city": ""}
        assert _dedup_key(row) == ("name_city", "acme|")

    def test_email_normalised_to_lowercase(self):
        row = {"email": "CEO@Acme.DE", "website": "", "company_name": "", "city": ""}
        assert _dedup_key(row) == ("email", "ceo@acme.de")


# ---------------------------------------------------------------------------
# Integration tests: import_leads_from_csv
# ---------------------------------------------------------------------------

class TestImportBasic:
    def test_imports_valid_rows(self, session, tmp_path):
        csv_file = write_csv(tmp_path / "leads.csv", [
            {"company_name": "Alpha Ltd", "email": "a@alpha.com", "city": "Berlin"},
            {"company_name": "Beta GmbH", "email": "b@beta.com", "city": "Munich"},
        ])
        result = import_leads_from_csv(session, csv_file)
        assert result.imported == 2
        assert result.skipped == 0
        assert result.failed == 0

    def test_imported_leads_have_status_new(self, session, tmp_path):
        csv_file = write_csv(tmp_path / "leads.csv", [
            {"company_name": "Alpha Ltd", "email": "a@alpha.com"},
        ])
        import_leads_from_csv(session, csv_file)
        lead = session.query(Lead).filter_by(company_name="Alpha Ltd").one()
        assert lead.status == LeadStatus.new

    def test_empty_csv_returns_zeros(self, session, tmp_path):
        csv_file = tmp_path / "empty.csv"
        csv_file.write_text("company_name,email\n", encoding="utf-8")
        result = import_leads_from_csv(session, csv_file)
        assert result.imported == 0
        assert result.skipped == 0
        assert result.failed == 0

    def test_missing_company_name_is_failed(self, session, tmp_path):
        csv_file = write_csv(tmp_path / "leads.csv", [
            {"company_name": "", "email": "x@x.com"},
            {"company_name": "Good Co", "email": "g@g.com"},
        ])
        result = import_leads_from_csv(session, csv_file)
        assert result.failed == 1
        assert result.imported == 1
        assert result.errors[0][0] == 2  # row number
        assert "company_name" in result.errors[0][1]

    def test_optional_fields_stored_as_none(self, session, tmp_path):
        csv_file = write_csv(tmp_path / "leads.csv", [
            {"company_name": "Sparse Co", "email": "", "city": "", "website": ""},
        ])
        import_leads_from_csv(session, csv_file)
        lead = session.query(Lead).filter_by(company_name="Sparse Co").one()
        assert lead.email is None
        assert lead.city is None
        assert lead.website is None

    def test_file_not_found_raises(self, session):
        with pytest.raises(ValueError, match="Cannot open CSV"):
            import_leads_from_csv(session, "/nonexistent/path/leads.csv")


class TestDeduplicationByEmail:
    def test_within_file_duplicate_email(self, session, tmp_path):
        csv_file = write_csv(tmp_path / "leads.csv", [
            {"company_name": "Alpha", "email": "same@alpha.com"},
            {"company_name": "Alpha Duplicate", "email": "same@alpha.com"},
        ])
        result = import_leads_from_csv(session, csv_file)
        assert result.imported == 1
        assert result.skipped == 1

    def test_against_existing_lead_email(self, session, existing_lead, tmp_path):
        csv_file = write_csv(tmp_path / "leads.csv", [
            {"company_name": "Copy", "email": existing_lead.email},
        ])
        result = import_leads_from_csv(session, csv_file)
        assert result.imported == 0
        assert result.skipped == 1

    def test_different_emails_both_imported(self, session, tmp_path):
        csv_file = write_csv(tmp_path / "leads.csv", [
            {"company_name": "Alpha", "email": "a@alpha.com"},
            {"company_name": "Beta", "email": "b@beta.com"},
        ])
        result = import_leads_from_csv(session, csv_file)
        assert result.imported == 2
        assert result.skipped == 0


class TestDeduplicationByWebsite:
    def test_within_file_duplicate_website(self, session, tmp_path):
        csv_file = write_csv(tmp_path / "leads.csv", [
            {"company_name": "Alpha", "email": "", "website": "https://alpha.de"},
            {"company_name": "Alpha Copy", "email": "", "website": "http://www.alpha.de/"},
        ])
        result = import_leads_from_csv(session, csv_file)
        assert result.imported == 1
        assert result.skipped == 1

    def test_against_existing_lead_website(self, session, existing_lead, tmp_path):
        csv_file = write_csv(tmp_path / "leads.csv", [
            {"company_name": "Copy", "email": "", "website": existing_lead.website},
        ])
        result = import_leads_from_csv(session, csv_file)
        assert result.imported == 0
        assert result.skipped == 1

    def test_website_protocol_variants_are_same(self, session, tmp_path):
        csv_file = write_csv(tmp_path / "leads.csv", [
            {"company_name": "Alpha", "email": "", "website": "http://alpha.de"},
            {"company_name": "Alpha HTTPS", "email": "", "website": "https://alpha.de"},
        ])
        result = import_leads_from_csv(session, csv_file)
        assert result.imported == 1
        assert result.skipped == 1


class TestDeduplicationByNameCity:
    def test_within_file_duplicate_name_city(self, session, tmp_path):
        csv_file = write_csv(tmp_path / "leads.csv", [
            {"company_name": "Acme GmbH", "email": "", "website": "", "city": "Berlin"},
            {"company_name": "Acme GmbH", "email": "", "website": "", "city": "Berlin"},
        ])
        result = import_leads_from_csv(session, csv_file)
        assert result.imported == 1
        assert result.skipped == 1

    def test_against_existing_lead_name_city(self, session, existing_lead, tmp_path):
        csv_file = write_csv(tmp_path / "leads.csv", [
            {
                "company_name": existing_lead.company_name,
                "email": "",
                "website": "",
                "city": existing_lead.city,
            }
        ])
        result = import_leads_from_csv(session, csv_file)
        assert result.imported == 0
        assert result.skipped == 1

    def test_same_name_different_city_both_imported(self, session, tmp_path):
        csv_file = write_csv(tmp_path / "leads.csv", [
            {"company_name": "Acme", "email": "", "website": "", "city": "Berlin"},
            {"company_name": "Acme", "email": "", "website": "", "city": "Munich"},
        ])
        result = import_leads_from_csv(session, csv_file)
        assert result.imported == 2
        assert result.skipped == 0

    def test_name_city_case_insensitive(self, session, tmp_path):
        csv_file = write_csv(tmp_path / "leads.csv", [
            {"company_name": "acme gmbh", "email": "", "website": "", "city": "berlin"},
            {"company_name": "Acme GmbH", "email": "", "website": "", "city": "BERLIN"},
        ])
        result = import_leads_from_csv(session, csv_file)
        assert result.imported == 1
        assert result.skipped == 1


class TestMixedRows:
    def test_summary_counts_all_outcomes(self, session, tmp_path):
        csv_file = write_csv(tmp_path / "leads.csv", [
            {"company_name": "Good Co", "email": "g@good.com"},           # imported
            {"company_name": "Good Co", "email": "g@good.com"},           # skipped (dup email)
            {"company_name": "", "email": "x@x.com"},                     # failed (no name)
            {"company_name": "Another", "email": "a@another.com"},        # imported
        ])
        result = import_leads_from_csv(session, csv_file)
        assert result.imported == 2
        assert result.skipped == 1
        assert result.failed == 1
