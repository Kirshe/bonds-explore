# BondScope India

A local Indian bond market dashboard with public-source collectors, downloaded-report imports, independent source histories, search, filters, sorting, bond details and CSV export. The normal application and CSV/XLSX import paths use Python's standard library. Legacy binary XLS files use the optional `xlrd` reader.

## Run

Requires Python 3.11+. Run commands from this checkout:

```bash
python -m bondscope serve
```

Open **http://127.0.0.1:8000**. Use `--port 8080` for a different port. The server binds to loopback and is intended for local use.

```bash
# Attempt all configured feeds; report success/failure separately for each.
python -m bondscope scrape

# Select sources; repeat --source to select several.
python -m bondscope scrape --source nse --source grip

# See which feeds actually have data and their collection status.
python -m bondscope sources

# Export all current source observations, including reference/inactive rows.
python -m bondscope export --output bonds.csv
```

A partial or failed scrape exits with status 1. Sources that fail keep their previous successful batch. The dashboard refreshes the selected source, or all sources when **All sources** is selected. Source cards distinguish configured feeds, collected records, snapshots, imports and failures. Configured does not mean successfully connected.

## Sources and collection limits

| Source ID | Coverage | Implemented collection/import path |
| --- | --- | --- |
| `bondbazaar` | State government, PSU, NBFC, tax-free and gold bond homepage sample | Public homepage HTML; duplicates removed by ISIN. |
| `nse` | CBM security master and corporate bond trade observations | Discover relevant published CSV/ZIP links from the debt reports page; otherwise try populated public trade tables. Supports downloaded CBM CSV/TSV/ZIP/XLSX/HTML reports. |
| `nsdl` | Debt instrument reference catalogue, including redeemed instruments | Discover the current entire-debt-list download from the NSDL page; parse XLS/XLSX/CSV/HTML. |
| `nsdl-redeemed` | Separate redeemed-instrument report | Discover the current debt-redeemed download, or import it. |
| `nsdl-defaults` | Separate redemption-default report | Discover the current default-report download, or import it. |
| `cdsl` | ISIN reference and status data | Parse populated public search-result tables or import a downloaded report. An empty dynamic search page is an explicit error. |
| `ccil` | Central government securities, SDLs and Treasury bills | Parse populated public market-watch HTML or a downloaded CSV/HTML/XLSX report. ISIN-less rows keep a source-scoped identity. |
| `grip` | Corporate bond public directory | Parse the first published directory table, including rating, coupon, maturity, payout and indicative yield. This is a bounded first-page sample, not an exhaustive directory crawl or a verified list of executable offers. |

Primary pages:

- [NSE debt reports](https://www.nseindia.com/all-reports-debt) and [corporate bond trades](https://www.nseindia.com/market-data/debt-market-reporting-corporate-bonds-traded-on-exchange).
- [NSDL detailed debt lists](https://nsdl.com/resources/data/detailed-list-debt-instruments).
- [CDSL corporate bond search](https://www.cdslindia.com/CorporateBond/SearchISIN.aspx).
- [CCIL / NDS-OM market watch](https://www.ccilindia.com/en/web/ccil/market-watch).
- [Grip bond directory](https://www.gripinvest.in/bond-directory/bonds).
- [Bondbazaar](https://www.bondbazaar.com/).

**Live collection of the new adapters has not been verified in this development environment:** shell DNS/network access is blocked. The adapters use published paths and report/table schemas checked through web research, with reconstructed fixtures for deterministic tests. NSE/CCIL/CDSL may deliver dynamic empty tables; blocked access, layout changes, missing links, unsupported spreadsheets and empty records are reported as failures, never as a successful empty market snapshot. Public-page visibility does not imply a supported public API or unrestricted redistribution rights.

NSE's adapter targets **CBM reports**, not the capital-market bond watch's rupee LTP feed. It does not reinterpret an unqualified `LTP` or `Price` column as a price per ₹100 face value. New feeds start empty until a successful fetch or report import; no synthetic fixture data is installed as live data.

## Import downloaded reports

When automatic access is unavailable, download the report from its original source and import it:

```bash
python -m bondscope import-report --source nse --file /path/to/cbm-report.csv --as-of 2026-11-06
python -m bondscope import-report --source nsdl --file /path/to/debt-instruments.xlsx --as-of 2026-11-04
python -m bondscope import-report --source nsdl-redeemed --file /path/to/debt-redeemed.csv --as-of 2026-11-04
python -m bondscope import-report --source nsdl-defaults --file /path/to/redemption-defaults.xlsx --as-of 2026-11-05
python -m bondscope import-report --source cdsl --file /path/to/cdsl-report.csv --as-of 2026-11-06
python -m bondscope import-report --source ccil --file /path/to/market-watch.html --as-of 2026-11-06
python -m bondscope import-report --source grip --file /path/to/directory.html
```

Use the date **printed on the report**, not today's date by default. Dates explicitly provided in rows or report filenames are retained. If no date is available, the report/quote date stays unknown. The file import time records local collection, not a network fetch or a newly issued quote.

Each import replaces the latest batch for that particular feed. Import a complete report for that feed. NSDL's catalogue, redeemed list and default list are separate feeds so importing one does not erase the others. Parse failures leave previous batches unchanged. A valid-but-empty status list is currently treated as a failure rather than clearing earlier status evidence; review the source error and report dates if such a case occurs.

An older or undated report cannot replace a source batch that already has a newer known report/quote date. For an undated download, supply its actual printed report date with `--as-of`.

CSV, TSV, CSV-in-ZIP, HTML tables and XLSX work without third-party packages. For NSDL's **legacy binary `.xls`** downloads, either save as CSV/XLSX in a spreadsheet application or install the optional reader into your Python environment:

```bash
python -m pip install -r requirements-excel.txt
# In a uv-managed virtual environment, alternatively:
uv pip install -r requirements-excel.txt
```

These optional packages are separate from the dependency-free application lockfile. Use the same Python environment to run BondScope. Excel date cells and percentage-formatted XLSX/XLS cells retain their types; numeric maturity serials are not guessed from plain CSV values.

## Data model and reliability

**One row is one source observation, not necessarily one distinct bond or buyable offer.** The dashboard counts distinct instruments separately from observations. ISIN joins allow reference enrichment without collapsing different platform/source quotes. CCIL observations with no published ISIN never acquire one by name guessing.

- **Separate yield bases:** source-quoted yield, NSE last-trade annualized yield, explicitly labelled traded YTM, explicitly labelled weighted-average YTM, generic weighted-average yield, CCIL last-traded yield and Grip indicative yield remain distinct. Generic `Yield` is not promoted to YTM. Grip's indicative calculations are at face value, not market offer prices.
- **No pooled median:** when multiple bases are present, select a yield basis to see its median/range/category chart. Sorting all bases groups values by basis. These unweighted statistics describe collected observations, not the whole market or a market index.
- **Prices:** `price_inr` and `price_pct_face` are separate. A CBM/CCIL price such as `101.25` per ₹100 face is not represented as ₹101.25 per bond. Accrued-interest/clean-versus-dirty conventions are not inferred when unspecified.
- **Missing fields:** unknown ratings, prices, maturity dates and timestamps stay unknown. The original Bondbazaar's ambiguous amount strings such as `1,000,00` remain raw text. Published tenure is not used to invent a maturity date.
- **Enrichment:** reference data fills missing maturity/coupon/rating/face-value/payout fields by exact ISIN. `field_sources` records the provider and report date. Conflicting existing values remain intact and are exposed in `conflicts` and bond details. Values are not treated as independently verified merely because they appear in a reference file.
- **Status:** dated reference evidence supplies outstanding/redeemed/defaulted status. The newest available report date takes precedence; with equal dates, defaulted/redeemed evidence takes precedence. Original source status is preserved separately from status after reference checks. Maturity dates on or before the Indian calendar day mark records matured. Unknown status does not mean available for purchase.
- **Inactive/defaulted observations:** matured/redeemed rows are hidden by default but can be included using the status filter. Defaulted rows remain visible with a badge. Gold, matured, redeemed and defaulted rows are excluded from yield statistics.
- **Freshness:** collection time, report date and quote timestamp remain separate. Collection age over 24 hours is shown on source cards; it is not a guarantee of quote freshness. Unknown or old quote/report dates remain visible even after a recent collection.

The repository retains the original 28-listing, manually transcribed Bondbazaar snapshot observed on **6 November 2026**. It is labelled `snapshot`, not a successful automated scrape. This source's quote dates are unknown and its published tenures may already be outdated.

## Scheduling and storage

No automatic job is installed. To collect every six hours on a network-enabled machine, configure your scheduler, for example:

```cron
0 */6 * * * cd /absolute/path/to/bondscrapper && /path/to/python -m bondscope scrape >> /tmp/bondscope-scrape.log 2>&1
```

Use a Python 3.11+ executable and install the optional XLS reader there if needed. Fetches are identified as `BondScope/0.1`, check robots rules, enforce source-specific five-minute refresh cooldowns, limit report sizes to 20 MB (80 MB expanded), and apply HTTP timeouts. NSDL feeds share request pacing. No login, CAPTCHA or access-control bypass is implemented. Sources may still require manual report downloads.

SQLite lives at `data/bonds.sqlite3` (gitignored). The original single-source schema migrates automatically, preserving history and existing source status. Each source has its own latest successful batch and failure state. A concurrent newer import supersedes an older in-flight refresh. Source observations retain stable IDs per source, instrument identity and yield basis.

```bash
python -m bondscope --db /tmp/bonds.sqlite3 serve --port 8080
```

The browser exports every filtered row across pages, in the selected order. CLI export includes all observations. Both retain per-row collection method, source status, units, dates and reference provenance. Spreadsheet formula prefixes are neutralized.

## Code and verification

- `bondscope/sources.py`: source registry, public collectors and provider-specific parsing.
- `bondscope/tabular.py`: CSV/ZIP/HTML/XLSX and optional XLS readers.
- `bondscope/models.py`: observation identity, dates, values and yield definitions.
- `bondscope/scraper.py`: original Bondbazaar adapter.
- `bondscope/store.py`: schema migration, independent source batches, refresh/import and enrichment.
- `bondscope/__main__.py`: local HTTP API and CLI.
- `web/`: responsive dashboard without external assets or a build step.

```bash
python -m unittest discover -s tests -v
node --check web/app.js
node tests/dashboard.test.cjs
```

Tests cover parser schemas, price/yield separation, absent ISINs, typed XLSX data, migration, partial refreshes, report import, independent cooldowns, status chronology, reference conflicts, API behavior, source/basis filters, distinct observation details and CSV provenance. Fixtures are synthetic/reconstructed, not captured production HTML/workbooks. Legacy XLS needs verification with `xlrd` and real source files. HTTP tests run real request/response handling through in-memory connections; JavaScript tests use a minimal DOM harness.

This sandbox denies local sockets and Chromium startup, so running-server and visual browser verification could not be completed here. Test locally after starting the server. The educational guide links to [RBI's government securities primer](https://www.rbi.org.in/Scripts/FAQView.aspx?Id=79). This application provides research information, not investment recommendations.
