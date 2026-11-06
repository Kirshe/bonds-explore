# BondScope India

A dependency-free Python scraper and local dashboard for public Indian bond listings. Includes an overview of the tracked listings, category yield comparisons, a searchable/sortable table, category/yield/payout filters, bond details, pagination, and filtered CSV export.

## Run

Requires Python 3.11 or later. No packages or API keys are needed. Run commands from this repository:

```bash
python -m bondscope serve
```

Open **http://127.0.0.1:8000**. Use `--port 8080` to choose another port. The server binds to loopback by default and is intended for local use.

Fetch the public source with **Refresh listings** or:

```bash
python -m bondscope scrape
python -m bondscope export --output bonds.csv
```

The scrape command exits with status 1 on a source/network error or refresh cooldown; it retains previous data. Browser CSV export includes all filtered results across pages, in the selected order. CLI export includes every listing. Both include collection method and source timestamps.

## Coverage and data provenance

The initial adapter reads the public [Bondbazaar homepage](https://www.bondbazaar.com/), covering its state government, PSU, NBFC, tax-free and sovereign gold bond sections. Bond ETFs are excluded. Bonds are deduplicated by ISIN. This is **one platform's public listing sample, not the entire Indian bond market**. The application makes no claim that a listing is tradable now.

`data/snapshot.json` contains 28 factual listings manually transcribed from that page during web research on **6 November 2026**. It lets the dashboard run immediately without network access. It is explicitly labelled **Snapshot**, not an automated scrape or live feed. The source's underlying quote date is unknown; its published tenures may already be outdated. A successful automated refresh replaces the snapshot; a failed refresh does not change its collection time or method.

This development environment blocks external DNS for shell processes, so a successful live fetch could not be verified here. Parser tests use a small reconstruction of the public heading/card structure, not a captured full-page HTML response. Run `python -m bondscope scrape` on a network-enabled machine to validate the adapter against the current page. A changed layout, denied access, or missing data is reported explicitly.

Field handling is intentionally conservative:

- **Yield:** stored as the source's generic quoted yield, **not assumed to be YTM**, effective yield, or a guaranteed return. Overview statistics are unweighted and describe the tracked listings only.
- **Gold bonds:** excluded from yield overview statistics, category yield charts, conventional yield filters and yield ordering. The table marks their yield `N/C`; details retain the source's figure. Their coupon is not total return.
- **Minimum investment:** malformed grouping such as `1,000,00` is preserved as raw source text; its INR value remains unknown. The parser accepts unambiguous Indian/international number formats.
- **Missing information:** ratings, prices, maturity dates, quote timestamps and secured status are not invented. Source tenure remains raw and is not converted into an inferred maturity date.
- **Freshness:** page observation time is separate from the unknown quote time. Collections older than 24 hours are flagged. Recently fetching a page does not establish freshness of the quotes it contains.

## Collection and scheduling

The scraper uses Python's standard-library `urllib`, checks `robots.txt`, identifies itself as `BondScope/0.1`, caps responses at 5 MB, applies request timeouts, and enforces a shared five-minute minimum refresh interval. Robots access errors fail closed; a missing robots file (404/410) allows the public page. It does not bypass logins, CAPTCHAs or access controls. A requested crawl delay above the supported interval causes collection to be skipped.

To collect periodically, use a scheduler such as cron on your own machine:

```cron
0 */6 * * * cd /absolute/path/to/bondscrapper && /usr/bin/python3 -m bondscope scrape >> /tmp/bondscope-scrape.log 2>&1
```

Choose a Python executable with version 3.11+. Failed scheduled runs leave the last collected batch in place and expose their errors in the dashboard.

## Storage and extension

SQLite lives at `data/bonds.sqlite3` (gitignored). Every successful refresh creates a historical batch. A transaction replaces the visible current batch only on success. CLI and server share the same refresh cooldown. To use a different database:

```bash
python -m bondscope --db /tmp/bonds.sqlite3 serve --port 8080
```

- `bondscope/scraper.py`: source adapter, HTML parser and normalization.
- `bondscope/store.py`: transactional batches, source status and overview statistics.
- `bondscope/__main__.py`: local HTTP API, scraper CLI and CSV export.
- `web/`: responsive HTML/CSS/JavaScript dashboard, with no external assets or build step.

To add another provider, implement a separate public-source adapter returning the same fields. Preserve provider-specific quotes rather than averaging or replacing different platform offers. The current store intentionally manages one source; multi-provider support requires per-source batch selection and refresh state.

## Verify

```bash
python -m unittest discover -s tests -v
node --check web/app.js
node tests/dashboard.test.cjs
```

Tests cover parsing both compact and visible cards, deduplication, excluded ETFs, missing fields, ambiguous amounts, robots rules, snapshot provenance, gold exclusion, refresh failure retention, refresh cooldown, transactional history, CSV output and HTTP routes.

HTTP tests exercise the real request parser and response handlers using in-memory connections. Dashboard interaction tests execute the actual JavaScript with a minimal DOM harness, covering search, composed filters, sorting, pagination, details, full-result CSV export, formula escaping and refresh errors. This sandbox denies local sockets and Chromium startup, so a running-server/browser check could not be completed here. Open the dashboard locally to check its final appearance.

The educational market guide links to [RBI's government securities FAQ](https://www.rbi.org.in/Scripts/FAQView.aspx?Id=79). This tool provides research information, not investment recommendations.
