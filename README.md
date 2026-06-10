# Outreach CRM

A lightweight, compliant B2B outreach CRM built with Python 3.12, SQLite, and Typer.

Designed for **manual, deliberate outreach** — not bulk sending. Rate limits and opt-out
fields are baked in from day one.

---

## Requirements

- Python 3.12+
- pip

---

## Setup

```bash
# 1. Clone / open the project folder
cd Outreach-CRM

# 2. Create a virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy and edit the environment file
cp .env.example .env
# Edit .env — set SENDER_NAME, SENDER_EMAIL, and limits

# 5. Initialise the database
python main.py init-db
```

---

## CLI Commands

| Command | Description |
|---|---|
| `init-db` | Create all database tables |
| `add-lead` | Add a lead (prompts for required fields) |
| `list-leads` | List leads; filter with `--status` / `--category` |
| `import-leads <file.csv>` | Bulk import leads from CSV with deduplication |
| `send-campaign <campaign_id>` | Dry-run a campaign (preview rendered emails) |
| `add-template` | Add a Jinja2 email template |
| `list-templates` | List all templates |
| `render-email <lead_id> <template_id>` | Preview a rendered email for a lead |
| `scrape-meinestadt` | Scrape security-service leads from meinestadt.de |

Run any command with `--help` for full option details.

### Examples

```bash
# Add a lead interactively
python main.py add-lead

# Add a lead non-interactively
python main.py add-lead --company "Acme Ltd" --email "ceo@acme.com" --city "Berlin" --category "SaaS"

# List only new leads
python main.py list-leads --status new

# Add a template from a file
python main.py add-template --name "cold_v1" --subject "Quick question for {{ company_name }}" --body-file templates/cold_outreach.j2

# Preview a rendered email (lead 1, template 1)
python main.py render-email 1 1

# Import leads from a CSV file
python main.py import-leads leads.csv

# Dry-run campaign 1 (default — no emails sent, no DB changes)
python main.py send-campaign 1

# Dry-run with a limit of 10 leads
python main.py send-campaign 1 --limit 10

# Attempt real send (blocked until implemented)
python main.py send-campaign 1 --no-dry-run

# Import a CSV with a different encoding (e.g. Excel export on Windows)
python main.py import-leads leads.csv --encoding cp1252
```

---

## Web UI

Start the server:

```bash
python main.py serve           # http://127.0.0.1:8000
python main.py serve --port 8001
```

Pages:

| URL | Description |
|---|---|
| `/` | Dashboard — lead and send counts |
| `/leads` | Lead list with status filter |
| `/leads/{id}` | Lead detail + event history |
| `/templates` | Template list + create form |
| `/leads/{id}/preview/{tpl_id}` | Rendered email preview |
| `/campaigns/{id}/dry-run` | Campaign dry-run preview |
| `/scraper` | Start a scrape job + recent job list |
| `/scraper/jobs/{id}` | Job detail with log (auto-refreshes while running) |

---

## Scraper — meinestadt.de Branchenbuch

Scrapes **Sicherheitsdienste** (security services) for up to 5 cities around Dresden
and saves leads directly into the database with deduplication.

### CLI

```bash
# Default: 5 cities, no per-city limit
python main.py scrape-meinestadt

# Custom cities + limit
python main.py scrape-meinestadt --cities dresden,freital,radebeul,pirna,meissen --limit-per-city 20

# Fast test run (2 leads per city)
python main.py scrape-meinestadt --limit-per-city 2
```

| Option | Default | Description |
|---|---|---|
| `--cities` | `freital,radebeul,pirna,meissen` | Comma-separated city slugs |
| `--limit-per-city` | none | Cap leads imported per city |
| `--delay` | `2.5` | Seconds between requests |

### Web UI

Go to `/scraper`, enter cities and limit, click **Start Scrape**.
The job runs synchronously; the page redirects to the job detail once done.
The job detail page auto-refreshes every 3 seconds while the job is `running`.

### Compliance

- `robots.txt` is checked before scraping begins; job aborts if disallowed.
- Clear `User-Agent` header identifies the bot.
- 2.5 s delay between every request (configurable).
- Only extracts **publicly visible** data from HTML (mailto links, tel links,
  external website links).
- Every imported lead has `source_url` pointing to the meinestadt.de detail page.

### Selectors used (meinestadt.de branchenbuch, verified 2025-06)

| Field | Selector |
|---|---|
| Listing items | `li.m-resultListEntries` |
| Company name | `h3.a-headline` |
| Detail URL | `a[href*='/company/']` |
| Address | `div.a-resultListMetainfoItem__text` (first) |
| Email (detail) | `a[href^='mailto:']` |
| Phone (detail) | `a[href^='tel:']` |
| Website (detail) | First `a[href^='http']` excluding meinestadt/social/app.link |
| Next page | `a` with text `vor` |

---

## Project Structure

```
crm/
  config.py              — env-var settings (rate limits, sender identity)
  database.py            — SQLAlchemy engine & session factory
  models.py              — ORM models: Lead, Template, Campaign, EmailEvent, ScrapeJob
  importer.py            — CSV import logic with deduplication
  email_renderer.py      — Jinja2 rendering with opt-out guard
  campaign_sender.py     — Dry-run campaign logic
  scraper_meinestadt.py  — meinestadt.de Branchenbuch scraper
  cli.py                 — Typer CLI commands
web/
  app.py                 — FastAPI routes
  templates/             — Jinja2 HTML templates
  static/style.css       — Minimal CSS
templates/
  cold_outreach.j2       — Sample outreach template
tests/
  conftest.py                  — pytest fixtures (in-memory SQLite session)
  test_importer.py             — 28 tests: import, dedup logic
  test_campaign_sender.py      — 29 tests: eligibility, rendering, rate limits
  test_web.py                  — 42 tests: all web routes
  test_scraper_parser.py       — 32 tests: HTML parser functions
main.py              — Entry point
.env.example         — Environment variable reference
requirements.txt
```

---

## CSV Import Format

`import-leads` expects a CSV with the following columns (header row required):

| Column | Required | Notes |
|---|---|---|
| `company_name` | Yes | Import fails this row if blank |
| `email` | No | Used as dedup key when present |
| `website` | No | Used as dedup key when email is absent |
| `city` | No | Used in dedup key when neither email nor website |
| `category` | No | |
| `phone` | No | |
| `source_url` | No | |
| `notes` | No | |

Extra columns are ignored. Missing optional columns default to empty.

**Deduplication priority:** email → website → company_name + city.
A row is skipped (not failed) when it matches an existing lead in the DB or an
earlier row in the same file.

```csv
company_name,city,category,website,email,phone,source_url,notes
Acme GmbH,Berlin,SaaS,https://acme.de,ceo@acme.de,+49123456,,
Beta AG,Munich,E-commerce,https://beta.de,,,https://source.example/beta,
Gamma Ltd,Hamburg,,,,+49987654,,no email available
```

---

## Lead Statuses

| Status | Meaning |
|---|---|
| `new` | Just added, not yet validated |
| `verified` | Email / contact confirmed |
| `opted_out` | Requested removal — never email again |
| `drafted` | Email draft created |
| `sent` | Email sent |
| `replied` | Lead replied |
| `bounced` | Email bounced |

---

## send-campaign

`send-campaign` runs in **dry-run mode by default** — it renders and previews emails without
sending anything or touching lead status.

| Option | Default | Description |
|---|---|---|
| `--dry-run` / `--no-dry-run` | `--dry-run` | Preview only; `--no-dry-run` is blocked until real sending is built |
| `--limit N` | none | Cap the number of leads processed on top of rate limits |

Rate limits are always enforced: the effective slot count is
`min(DAILY_SEND_LIMIT - sent_today, HOURLY_SEND_LIMIT - sent_this_hour[, --limit])`.

---

## Compliance Notes

- **Opt-out is enforced at render time.** `render-email` refuses to render for opted-out leads.
- Rate limits (`DAILY_SEND_LIMIT`, `HOURLY_SEND_LIMIT`) are surfaced in every render preview so you stay within safe bounds.
- The sample template includes an unsubscribe notice — keep it in your outreach.
- All email events are logged in `email_events` for a full audit trail.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///crm.db` | SQLAlchemy database URL |
| `SENDER_NAME` | _(empty)_ | Your name, injected into templates |
| `SENDER_EMAIL` | _(empty)_ | Your email, injected into templates |
| `DAILY_SEND_LIMIT` | `50` | Maximum emails per day |
| `HOURLY_SEND_LIMIT` | `10` | Maximum emails per hour |
