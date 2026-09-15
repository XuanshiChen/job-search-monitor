# Job Search Monitor

[中文说明](README.zh-CN.md)

Job Search Monitor is a local-first personal job-search command center for power,
protection and control, substation, utility, and adjacent electrical-engineering roles.
It repeatedly scans configured employer career platforms, normalizes and scores jobs,
deduplicates repeated sightings, preserves history in SQLite, creates a daily digest,
and provides a Streamlit dashboard for review and application tracking.

The current strategy intentionally monitors only 20 highly relevant employer career
sites. Email aggregation and LinkedIn, Indeed, Glassdoor, and Job Bank alert sources
are disabled. The local sample source remains available as a disabled architecture demo.

## What is included

- Configurable critical and three-tier company priority system
- Greenhouse, Lever, SmartRecruiters, Workday, SuccessFactors, Avature, Workable,
  SelectMinds, Jobsyn, Jobvite, Jibe, AEM Job List, Paradox, Cornerstone/CSOD,
  Eightfold, iCIMS, Taleo Business Edition, and generic JSON-LD/HTML adapters.
  The legacy job-alert email adapter remains in the codebase but is disabled.
- Respectful HTTP timeouts, request spacing, retry/backoff, and independent source failures
- Configurable bounded concurrency across independent company sources; database writes stay sequential
- Persistent SQLite data at `data/jobs.db`
- Four-stage deduplication with source-occurrence tracking
- Transparent 0–100 scoring and stored human-readable reasons
- NEW, UPDATED, REOPENED, CLOSED, and manual status event history
- Desktop alerts for new jobs above a configurable threshold
- Markdown daily report and configurable daily application target
- Streamlit dashboard with filters, job details, notes, statuses, and basic analytics
- CLI for initialization, scanning, reporting, status updates, and dashboard launch
- Mocked and local tests; tests do not crawl real career sites

## Architecture

This is a modular monolith: simple to operate locally, while keeping source-specific
logic away from scoring, storage, and UI code.

```text
Company YAML configuration
        |
        v
Platform adapter -> normalized JobCandidate
        |
        v
Deduplicator -> scorer -> repository -> SQLite
                                  |
                     +------------+-------------+
                     |                          |
                   CLI/report                 dashboard
```

`JobOccurrence` preserves every source identity/URL associated with a canonical job.
`JobEvent` preserves discovery, meaningful updates, reopening, closing, and manual
status changes. Removed jobs are never deleted.

### Deduplication order

1. Canonical company plus external job ID, including prior source occurrences
2. Canonical job URL, after removing tracking parameters and fragments
3. Company plus normalized title plus normalized location fingerprint
4. Conservative same-company title/location similarity fallback

A repeat sighting updates `date_last_seen`. A material content change creates an
UPDATED event. A job missing longer than `close_missing_after_days` is marked CLOSED,
but only after that company's source completed successfully. A failed scan can never
close all of a company's jobs.

### Scoring

The scorer adds configured contributions for company tier, role category, entry-level
language, technical keywords, preferred geography, and remote-work preference. It
applies explicit experience and seniority penalties, caps the final score to 0–100,
and stores each reason and concern. `Senior` in a description is a small penalty, not
an automatic rejection. Hard exclusions are limited to the configured title phrases.

Edit the weights and thresholds in `config/settings.yaml` and the phrases in
`config/keywords.yaml`. No scoring rule is hidden in a hosted service or paid model.

## Project layout

```text
job-search-monitor/
├── main.py
├── README.md
├── requirements.txt
├── .env.example
├── config/
│   ├── companies.yaml
│   ├── keywords.yaml
│   ├── settings.yaml
│   └── sample_jobs.yaml
├── data/                    # persistent SQLite database (created locally)
├── logs/                    # rotating structured JSON log
├── reports/                 # generated daily Markdown reports
├── scripts/
│   └── run_daily.ps1
├── src/
│   ├── config/
│   ├── models/
│   ├── database/
│   ├── sources/
│   │   ├── base.py
│   │   ├── greenhouse.py
│   │   ├── lever.py
│   │   ├── linkedin_email.py
│   │   ├── successfactors.py
│   │   ├── avature.py
│   │   ├── workable.py
│   │   ├── selectminds.py
│   │   ├── jobsyn.py
│   │   ├── jobvite.py
│   │   ├── jibe.py
│   │   ├── aem_joblist.py
│   │   ├── cornerstone.py
│   │   ├── eightfold.py
│   │   ├── icims.py
│   │   ├── taleo_business.py
│   │   ├── paradox.py
│   │   ├── smartrecruiters.py
│   │   ├── workday.py
│   │   ├── generic.py
│   │   └── sample.py
│   ├── matching/
│   ├── services/            # scanner, repository, reports, email sync, notifications
│   ├── dashboard/
│   └── utils/
└── tests/
```

## Use on another computer

The private GitHub repository contains code and configuration only. The SQLite
database, logs, reports, imported email files, and `.env` are intentionally excluded.
On another Windows computer:

```powershell
git clone https://github.com/XuanshiChen/job-search-monitor.git
cd job-search-monitor
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python main.py init
streamlit run src/dashboard/app.py
```

This creates a fresh local history. GitHub synchronizes the application and company
configuration; it does not synchronize application statuses between computers.

## Installation

Python 3.12 or newer is required. In PowerShell:

```powershell
cd D:\job_search
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
python main.py init
```

If PowerShell blocks activation, you can call `.venv\Scripts\python.exe` directly in
every command. Activation is only a convenience.

## First run

```powershell
python main.py init
streamlit run src/dashboard/app.py
```

On the first Dashboard visit each calendar day, the application scans every enabled
official company source. It does not connect to Gmail or job aggregators. Filters and
other widget interactions do not trigger repeated scans. Use **Refresh jobs now** in
the sidebar when you want another refresh. The dashboard opens at
`http://localhost:8501` by default.
During either refresh, the dashboard shows the current stage, the employer/source being
checked, completed source count, and an overall progress bar.

In the Jobs table, click **Open job** to open the original application page. Select any
row to load it into **Job detail and status**, where you can change its status and save
notes. Marking a job `APPLIED` records the application date automatically.

After finishing your applications for the day, open the **Daily report** tab and click
**Generate today's report**. The dashboard previews the report, saves it under
`reports/`, and provides a Markdown download. The equivalent CLI command remains
available:

```powershell
python main.py report --print
```

Equivalent convenience commands are available:

```powershell
python main.py dashboard
python main.py scan --company "Hydro One"
python main.py scan --new-only
python main.py status JOB_INTERNAL_ID APPLIED --notes "Submitted on company site"
```

`--new-only` changes console output only; all fetched postings are still processed so
`date_last_seen`, changes, and closure detection remain correct.

## Configuration

### Preconfigured target employers

Twenty public employer sources are enabled: Hydro One, IESO, Bruce Power, Ontario Power
Generation, Siemens, SEL, GE Vernova, Toronto Hydro, BBA Consultants, AltaLink, Hatch,
Tetra Tech, Burns & McDonnell, Eaton, Sargent & Lundy, Black & Veatch, ABB, Schneider
Electric, Hitachi Energy, and Stantec.

BC Hydro is also configured as a critical employer, but its current public search is an
SAP WebDynpro application without a stable public listing feed. WSP's public listing
currently presents a Cloudflare verification page to unattended clients. Both remain
disabled. The monitor does not connect to LinkedIn, Indeed, Glassdoor, Job Bank, or
Gmail in the current official-company-only strategy.

### General settings

Edit `config/settings.yaml` for:

- daily application goal and report/notification score thresholds
- whether the dashboard scans on the first visit of each local calendar day
- preferred province tiers and cities
- local timezone used for daily reports and application-goal boundaries
- Canada-wide and international behavior
- remote/hybrid/on-site weights
- stale-job closure and similarity thresholds
- request timeout, retry, backoff, interval, and User-Agent
- maximum concurrent company sources (`scanner.max_concurrent_sources`, default `4`)
- desktop/email notification switches

Locations are ordinary YAML values, not hard-coded application branches. Province
abbreviations returned by sources are normalized, while configured tiers determine
their score.

### Keywords and scoring vocabulary

Edit `config/keywords.yaml` to change role categories, entry-level phrases, technical
keyword values, hard title exclusions, and soft penalties. Title role classification
and every scoring contribution are visible in each job's match explanation.

### Add a company

First identify the recruiting platform by inspecting the employer's public careers
URL or its network/API behavior. Then add the company to the matching tier in
`config/companies.yaml`. Companies under `critical` are scanned first and receive the
strongest configured bonus.

Greenhouse:

```yaml
companies:
  tier_1:
    - name: Example Energy
      careers_url: https://boards.greenhouse.io/exampleenergy
      source_type: greenhouse
      options:
        board_token: exampleenergy
        # Optional; adds one public detail request per job to obtain first_published.
        fetch_details: false
```

Lever:

```yaml
  tier_2:
    - name: Example Grid
      careers_url: https://jobs.lever.co/examplegrid
      source_type: lever
      options:
        site: examplegrid
        # Use region: eu for jobs.eu.lever.co boards.
        region: global
```

SmartRecruiters:

```yaml
  tier_2:
    - name: Example Automation
      careers_url: https://careers.smartrecruiters.com/ExampleAutomation
      source_type: smartrecruiters
      options:
        company_identifier: ExampleAutomation
        fetch_details: true
```

Workday tenants vary. Supply the public host, tenant, and career-site name explicitly:

```yaml
  tier_1:
    - name: Example Power
      careers_url: https://example.wd5.myworkdayjobs.com/en-US/Careers
      source_type: workday
      options:
        host: https://example.wd5.myworkdayjobs.com
        tenant: example
        site: Careers
        locale: en-US
        fetch_details: true
```

For recognizable URLs, `source_type: auto` selects Greenhouse, Lever,
SmartRecruiters, or Workday. Explicit platform configuration is more predictable and
is recommended for important employers.

Public API references: [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html),
[Lever Postings API](https://github.com/lever/postings-api), and
[SmartRecruiters Posting API](https://developers.smartrecruiters.com/docs/endpoints).
Workday's public career endpoint is tenant-specific and is therefore treated as a
configurable best-effort adapter rather than a universal documented API contract.

Generic career pages are supported first through `JobPosting` JSON-LD. For a stable
public listing page without JSON-LD, configure CSS selectors:

```yaml
  tier_3:
    - name: Example Utility
      search_url: https://careers.example.ca/jobs
      source_type: generic_html
      options:
        selectors:
          item: article.job-card
          title: h2
          link: a.job-link
          location: .job-location
          description: .summary
          id_attribute: data-job-id
```

Do not configure authenticated pages or endpoints that require bypassing CAPTCHAs or
anti-bot controls. The monitor only uses public APIs, structured page data, or public
HTML.

## Disabled legacy job-alert email integration

The current strategy does not scrape, automate, or import alerts from LinkedIn, Indeed,
Glassdoor, or Job Bank, and it does not connect to Gmail. The legacy adapter and manual
commands remain in the codebase for reversibility, but both the source and IMAP setting
are disabled by default.
No email credentials are required. The Dashboard and `scripts/run_daily.ps1` scan
official company sources directly and never invoke email synchronization.

## Add a new source adapter

1. Create a module in `src/sources/` with a class derived from `JobSource`.
2. Implement `fetch_jobs()` and return `list[JobCandidate]`.
3. Use the shared respectful HTTP client from the base class.
4. Normalize all platform fields; do not write directly to the database.
5. Register the adapter in `src/sources/registry.py`.
6. Add fixture-based tests that mock network responses.

If several employers use the same recruitment platform, add company configuration,
not employer-specific scraper modules. A company-specific adapter should be a last
resort for a genuinely unique public site.

## Notifications and secrets

Desktop notification is enabled by default. Only newly discovered jobs at or above
`urgent_notification_score` trigger it, with a per-scan cap. Change this in
`config/settings.yaml` if Windows notifications are unavailable.

Email is implemented but disabled. To enable it, copy `.env.example` to `.env`, fill
the SMTP values, and set `notifications.email_enabled: true`. `.env` and the database
are ignored by Git. Never place credentials in YAML or source code.

Legacy IMAP variables and imported `.eml` files remain ignored by Git. They are not
used in the current strategy.

## Windows Task Scheduler

The supplied script scans the 20 enabled official company sources. It deliberately
leaves the daily report for you to generate after finishing your applications and
stops if the scan fails:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\job_search\scripts\run_daily.ps1"
```

To configure Task Scheduler:

1. Open **Task Scheduler** and choose **Create Task**.
2. On **General**, name it `Job Search Monitor` and select **Run only when user is logged on** if desktop notifications are wanted.
3. On **Triggers**, add a daily trigger, for example 7:00 AM.
4. On **Actions**, choose **Start a program**.
5. Program: `powershell.exe`
6. Arguments: `-NoProfile -ExecutionPolicy Bypass -File "D:\job_search\scripts\run_daily.ps1"`
7. Start in: `D:\job_search`
8. On **Settings**, enable **Run task as soon as possible after a scheduled start is missed** and prevent overlapping instances.
9. Use **Run** once, then inspect `logs/job_monitor.log` and the dashboard.

At the end of your application session, create the report yourself so its application
counts include everything you marked `APPLIED` that day:

```powershell
python main.py report --print
```

Update the paths in `scripts/run_daily.ps1` if the project is moved. The same CLI can
later be called by cron or a VPS. Avoid GitHub Actions unless storing the persistent
SQLite database and job history securely is solved first.

## Testing

```powershell
python -m pytest
python -m compileall -q main.py src
```

Tests use temporary SQLite databases and mocked/local source data. They cover
normalization, deduplication hierarchy, updates and history, scoring, company priority,
location configuration, source parsing, status/application timestamps, scans, and
reports.

## Data and operational behavior

- SQLite uses foreign keys, WAL mode, and a busy timeout.
- Schema initialization is idempotent and records a schema version.
- Logs are JSON lines in a rotating `logs/job_monitor.log` file.
- Each employer commits independently; one failed source does not stop the run.
- Each job commits independently; a malformed posting does not discard its siblings.
- Application counts use `applied_date`, which is set only after an explicit APPLIED action.
- Changing away from APPLIED does not erase the original applied timestamp.
- Historical jobs are closed, not deleted.

Back up `data/jobs.db` periodically while no scan/dashboard write is active. It contains
your complete discovery, notes, and application history.

## Troubleshooting

**A source reports no jobs**

Confirm the public career URL and platform identifier. Workday tenants commonly use a
different tenant value than their public brand. For a generic page, inspect whether
jobs are loaded from an API after page load; static selectors cannot see content that
is never present in the returned HTML.

**A source returns 403, 429, CAPTCHA, or authentication**

Disable it. Do not reduce request spacing or try to bypass access controls. Look for an
employer-provided public API/feed or monitor that company manually.

**The dashboard cannot import `src`**

Launch it from the project root exactly as shown. `app.py` also adds the project root
to its import path for direct Streamlit execution.

**Desktop notifications do not appear**

Run while logged in, allow notifications for Python/PowerShell, or set
`desktop_enabled: false`. A notification failure is logged and never fails a scan.

**A duplicate was not merged**

Check whether the company names differ. Keep one canonical company name in
configuration and use its `aliases` list for documentation/future aggregator mapping.
The fuzzy threshold is intentionally conservative to avoid merging distinct openings
in different cities.

**A job has a weak description**

Some listing APIs return summaries only. Enable `fetch_details` where supported.
Cross-source merges retain the longer known description and the earliest known posting
date.

## Scope and roadmap

The MVP is deliberately local and deterministic: core database/history, adapters,
scoring, deduplication, CLI, report, notifications, dashboard, and tests are included.
The configured priority employers and public platform adapters are operational. Later
phases can add higher-frequency critical-company runs, richer analytics, more email
providers, and fully local resume skill extraction. The base system does not require
a paid API or LLM.
