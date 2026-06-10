"""Tests for crm/scraper_meinestadt.py parser functions (no network calls)."""
import pytest

from crm.scraper_meinestadt import (
    _matches_keywords,
    find_next_page_url,
    parse_detail_page,
    parse_listing_page,
)

# ---------------------------------------------------------------------------
# Sample HTML fixtures
# ---------------------------------------------------------------------------

LISTING_ARTICLES = """
<html><body>
<article>
  <h2><a href="/company/test-sicherheit/123">Test Sicherheit GmbH</a></h2>
  <address>Hauptstrasse 1, 01069 Dresden</address>
</article>
<article>
  <h2><a href="/company/wach-service/456">Wach- und Schliessgesellschaft mbH</a></h2>
  <p>Ringstrasse 5, 01069 Dresden</p>
</article>
</body></html>
"""

LISTING_SEARCH_RESULT_ITEMS = """
<html><body>
<div class="search-result-item">
  <h2 class="company-name"><a href="/company/dresden-security/789">Dresden Security AG</a></h2>
  <div class="address">Koennigerstrasse 10, 01067 Dresden</div>
</div>
<div class="search-result-item">
  <h3><a href="/company/schutz-gmbh/999">Schutz GmbH</a></h3>
  <div class="address">Am Markt 2, 01067 Dresden</div>
</div>
</body></html>
"""

LISTING_DATA_CY = """
<html><body>
<div data-cy="business-results-item">
  <h2><a href="/firmen/wachschutz-xyz/111">Wachschutz XYZ</a></h2>
  <address>Bahnhofstr. 3, 01069 Dresden</address>
</div>
</body></html>
"""

LISTING_EMPTY = "<html><body><p>Keine Ergebnisse gefunden.</p></body></html>"

DETAIL_FULL = """
<html><body>
<a href="mailto:info@test-sicherheit.de">info@test-sicherheit.de</a>
<a href="https://www.test-sicherheit.de">www.test-sicherheit.de</a>
<a href="tel:+4935112345">+49 351 12345</a>
</body></html>
"""

DETAIL_NO_EMAIL = """
<html><body>
<a href="https://www.sicherheitsgmbh.de">sicherheitsgmbh.de</a>
<a href="tel:+493512222">+49 351 2222</a>
</body></html>
"""

DETAIL_NO_WEBSITE = """
<html><body>
<a href="mailto:kontakt@wachdienst.de">kontakt@wachdienst.de</a>
<a href="tel:+491234567">0123 4567</a>
</body></html>
"""

DETAIL_PHONE_IN_SPAN = """
<html><body>
<span class="phone">030 / 12 34 567</span>
</body></html>
"""

DETAIL_EMAIL_WITH_QUERY = """
<html><body>
<a href="mailto:contact@company.de?subject=Anfrage">contact@company.de</a>
</body></html>
"""

DETAIL_EXCLUDES_SOCIAL = """
<html><body>
<a href="https://www.facebook.com/company">Facebook</a>
<a href="https://www.google.de/maps">Maps</a>
<a href="https://company.de">company.de</a>
</body></html>
"""

DETAIL_EXCLUDES_MEINESTADT = """
<html><body>
<a href="https://www.meinestadt.de/partner">Partner</a>
<a href="https://security-firm.de">security-firm.de</a>
</body></html>
"""

DETAIL_EXCLUDES_APPLINK = """
<html><body>
<a href="https://nz8ie.app.link/portal-aktuelles-teaser">App</a>
<a href="https://www.wachschutz-gmbh.de">Unsere Website</a>
</body></html>
"""

PAGINATION_WITH_REL_NEXT = """
<html><body>
<a rel="next" href="/dresden/brazl/100-19055?page=2">Naechste Seite</a>
</body></html>
"""

PAGINATION_WITH_VOR = """
<html><body>
<a href="/dresden/brazl/100-19055-19077-71497?page=2">vor</a>
</body></html>
"""

PAGINATION_WITH_WEITER = """
<html><body>
<div class="pagination">
  <a href="/firmen?page=1">1</a>
  <a href="/firmen?page=2">Weiter</a>
</div>
</body></html>
"""

PAGINATION_NO_NEXT = """
<html><body>
<div class="pagination">
  <a href="/firmen?page=1">1</a>
  <a href="/firmen?page=2">2</a>
</div>
</body></html>
"""


# ---------------------------------------------------------------------------
# parse_listing_page
# ---------------------------------------------------------------------------

class TestParseListingPage:
    def test_parses_article_entries(self):
        entries = parse_listing_page(LISTING_ARTICLES, "dresden")
        assert len(entries) == 2

    def test_extracts_company_name_from_article(self):
        entries = parse_listing_page(LISTING_ARTICLES, "dresden")
        names = [e["company_name"] for e in entries]
        assert "Test Sicherheit GmbH" in names
        assert "Wach- und Schliessgesellschaft mbH" in names

    def test_extracts_detail_url(self):
        entries = parse_listing_page(LISTING_ARTICLES, "dresden")
        urls = [e["detail_url"] for e in entries]
        assert any("/company/test-sicherheit/123" in u for u in urls)

    def test_detail_url_is_absolute(self):
        entries = parse_listing_page(LISTING_ARTICLES, "dresden")
        for e in entries:
            if e["detail_url"]:
                assert e["detail_url"].startswith("http")

    def test_extracts_address_from_article(self):
        entries = parse_listing_page(LISTING_ARTICLES, "dresden")
        assert any("Hauptstrasse" in e["address"] for e in entries)

    def test_parses_search_result_items(self):
        entries = parse_listing_page(LISTING_SEARCH_RESULT_ITEMS, "dresden")
        assert len(entries) == 2
        assert any(e["company_name"] == "Dresden Security AG" for e in entries)

    def test_parses_data_cy_items(self):
        entries = parse_listing_page(LISTING_DATA_CY, "dresden")
        assert len(entries) == 1
        assert entries[0]["company_name"] == "Wachschutz XYZ"

    def test_city_is_preserved(self):
        entries = parse_listing_page(LISTING_ARTICLES, "freital")
        assert all(e["city"] == "freital" for e in entries)

    def test_category_is_sicherheitsdienste(self):
        entries = parse_listing_page(LISTING_ARTICLES, "dresden")
        assert all(e["category"] == "Sicherheitsdienste" for e in entries)

    def test_empty_page_returns_empty_list(self):
        assert parse_listing_page(LISTING_EMPTY, "dresden") == []

    def test_empty_html_returns_empty_list(self):
        assert parse_listing_page("<html><body></body></html>", "dresden") == []


# ---------------------------------------------------------------------------
# parse_detail_page
# ---------------------------------------------------------------------------

class TestParseDetailPage:
    def test_extracts_email(self):
        result = parse_detail_page(DETAIL_FULL)
        assert result["email"] == "info@test-sicherheit.de"

    def test_extracts_website(self):
        result = parse_detail_page(DETAIL_FULL)
        assert result["website"] == "https://www.test-sicherheit.de"

    def test_extracts_phone_from_tel_link(self):
        result = parse_detail_page(DETAIL_FULL)
        assert result["phone"] == "+4935112345"

    def test_missing_email_returns_empty(self):
        result = parse_detail_page(DETAIL_NO_EMAIL)
        assert result["email"] == ""

    def test_missing_website_returns_empty(self):
        result = parse_detail_page(DETAIL_NO_WEBSITE)
        assert result["website"] == ""

    def test_phone_in_span(self):
        result = parse_detail_page(DETAIL_PHONE_IN_SPAN)
        assert result["phone"] == "030 / 12 34 567"

    def test_email_query_params_stripped(self):
        result = parse_detail_page(DETAIL_EMAIL_WITH_QUERY)
        assert result["email"] == "contact@company.de"
        assert "?" not in result["email"]

    def test_excludes_social_media_links(self):
        result = parse_detail_page(DETAIL_EXCLUDES_SOCIAL)
        assert "facebook" not in result["website"]
        assert result["website"] == "https://company.de"

    def test_excludes_meinestadt_links(self):
        result = parse_detail_page(DETAIL_EXCLUDES_MEINESTADT)
        assert "meinestadt" not in result["website"]
        assert result["website"] == "https://security-firm.de"

    def test_excludes_app_link_urls(self):
        result = parse_detail_page(DETAIL_EXCLUDES_APPLINK)
        assert "app.link" not in result["website"]
        assert result["website"] == "https://www.wachschutz-gmbh.de"

    def test_all_empty_on_blank_page(self):
        result = parse_detail_page("<html><body></body></html>")
        assert result == {"email": "", "website": "", "phone": ""}


# ---------------------------------------------------------------------------
# find_next_page_url
# ---------------------------------------------------------------------------

class TestFindNextPageUrl:
    def test_finds_vor_link(self):
        url = find_next_page_url(PAGINATION_WITH_VOR)
        assert url is not None
        assert "page=2" in url

    def test_vor_href_is_absolute(self):
        url = find_next_page_url(PAGINATION_WITH_VOR)
        assert url.startswith("http")

    def test_finds_rel_next_link(self):
        url = find_next_page_url(PAGINATION_WITH_REL_NEXT)
        assert url is not None
        assert "page=2" in url

    def test_rel_next_href_is_absolute(self):
        url = find_next_page_url(PAGINATION_WITH_REL_NEXT)
        assert url.startswith("http")

    def test_finds_weiter_link(self):
        url = find_next_page_url(PAGINATION_WITH_WEITER)
        assert url is not None
        assert "page=2" in url

    def test_returns_none_when_no_next(self):
        assert find_next_page_url(PAGINATION_NO_NEXT) is None

    def test_returns_none_on_empty_html(self):
        assert find_next_page_url("<html><body></body></html>") is None


# ---------------------------------------------------------------------------
# _matches_keywords
# ---------------------------------------------------------------------------

class TestMatchesKeywords:
    KW = ["security", "sicherheit", "secur", "wach"]

    def test_matches_security_exact(self):
        assert _matches_keywords("Dresden Security GmbH", self.KW)

    def test_matches_sicherheit(self):
        assert _matches_keywords("Sicherheitsdienste AG", self.KW)

    def test_matches_secur_substring(self):
        assert _matches_keywords("Securitas Deutschland", self.KW)

    def test_matches_wach(self):
        assert _matches_keywords("Wachdienst Elbe", self.KW)

    def test_matches_case_insensitive(self):
        assert _matches_keywords("SECURITY SERVICE GMBH", self.KW)
        assert _matches_keywords("sICHERHEIT und Bewachung", self.KW)

    def test_no_match_returns_false(self):
        assert not _matches_keywords("Blumenhaus Müller", self.KW)
        assert not _matches_keywords("IT Consulting GmbH", self.KW)

    def test_empty_keywords_returns_false(self):
        assert not _matches_keywords("Security GmbH", [])

    def test_partial_match_in_longer_word(self):
        assert _matches_keywords("Sicherheitstechnik GmbH", self.KW)

    def test_single_keyword_list(self):
        assert _matches_keywords("Wachschutz Pirna", ["wach"])
        assert not _matches_keywords("Hausmeister Service", ["wach"])
