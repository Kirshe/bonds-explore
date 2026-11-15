# Bonds Explore

An Indian bond market dashboard that can be published as a static GitHub Pages site, with a local CLI for collection, downloaded-report imports, builds and previews. Includes independent source histories, search, filters, sorting, bond details and CSV export. The normal application and CSV/XLSX import paths use Python's standard library. Legacy binary XLS files use the optional `xlrd` reader.

## Build and test locally

Requires Python 3.11+. Run commands from this checkout; no GitHub account, Actions runner or installed packages are needed for CSV/XLSX data and static builds.

```bash
# Build immediately from local data. No network collection is attempted.
python -m bondscope build

# Serve exactly the static files that GitHub Pages will publish.
python -m bondscope preview
```

Open **http://127.0.0.1:8000**. This serves `dist/`, which contains the dashboard and `market.json`. There is no scraper API in static preview. To simulate the repository subpath used by a project Pages site:

```bash
python -m bondscope preview --base-path /bonds-explore/ --port 8080
```

Open **http://127.0.0.1:8080/bonds-explore/**. Assets and data URLs are relative, so the same bundle also works at a user-site root or custom domain.

To collect fresh data and build in one command:

```bash
python -m bondscope build --scrape
python -m bondscope preview

# Limit collection to selected feeds, or choose another output directory.
python -m bondscope build --scrape --source nse --source grip --output dist
```

`build --scrape` still builds a valid site if a provider fails, retaining its last-good rows and exposing its error. It exits 0 when the static bundle is successfully built. Add `--strict` to exit 1 for partial/failed collection even if the retained-data build succeeds. Invalid state or a fatal build error exits 2. A plain `build` does not collect data. Successful building or recent publication does not imply fresh quotes.

The website opens directly to the bond explorer, with search, filters, sorting, details and CSV export. Collection mechanics and source coverage are documented below rather than displayed as separate overview or source panels.

The static site loads `market.json` when opened and has no refresh button. Reload the browser page to fetch the latest publication. Collection runs separately in the scheduled workflow. Search, filters, sorting, details and CSV export run in the browser. The publication timestamp is shown separately from source collection/quote dates. Collection-age indicators and maturity exclusions are recalculated at browser load, so an old publication does not freeze them in time.

## Local collector and optional live server

The collection/import/export CLI remains available independently of static hosting. For the local API-backed dashboard with an on-demand scrape button:

```bash
python -m bondscope serve
```

Open **http://127.0.0.1:8000**. Use `--port 8080` for another port. The API server and static preview bind to loopback by default. The local API server is optional; it is not deployed to GitHub Pages.

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

A partial or failed scrape exits with status 1. Sources that fail keep their previous successful batch. The local API-backed dashboard refreshes the selected source, or all sources when **All sources** is selected. The static dashboard reads published JSON on page load. The website focuses on the bond explorer. Source links, collection method, dates, freshness and collection status are available in each bond’s details. Use `python -m bondscope sources` for a feed-by-feed summary. Configured does not mean successfully connected.

## Publish to GitHub Pages

The repository includes [`.github/workflows/pages.yml`](.github/workflows/pages.yml). To enable deployment:

1. Push this checkout, including the workflow, to GitHub with `main` or `master` as the default branch.
2. In **Settings → Pages → Build and deployment**, set **Source** to **GitHub Actions**.
3. Make sure repository/organisation Actions policies allow the workflow's `contents: write` permission and creation/updates of the `bondscope-data` branch. No personal access token or custom secret is required.
4. Open **Actions → Update bond data and publish Pages → Run workflow** on the default branch. The deployment job reports the Pages URL.

The workflow runs once daily at **00:17 UTC** (**05:47 IST**), as well as on pushes to the default `main`/`master` branch. A manual run has a **scrape** checkbox; disable it to republish retained data without contacting providers. For a differently named default branch, update the workflow's push branch list. Only the default branch is deployed.

Each run:

1. Tests the application and installs the optional XLS reader on the runner.
2. Restores the previous source state from the separate `bondscope-data` branch into a fresh temporary SQLite database.
3. Attempts collection, retaining each unsuccessful source's last-good batch.
4. Generates `dist/` and writes updated source state outside that public directory.
5. Commits `state.json` to `bondscope-data`, then uploads and deploys only `dist/` to Pages.

The data branch is created automatically on the first successful build. It contains only resumable source-state JSON; application code stays on the default branch. State persistence does not rely on expiring Actions artifacts or caches. Workflow concurrency serialises the whole cycle. Failure to read existing remote state is fatal rather than silently resetting the dataset. Invalid state, test/build errors, or a failed state push stop publication and leave the previously deployed site in place.

This publishes the original labelled snapshot if no source has ever collected successfully. First publication is not evidence of a successful live scrape. Source failures are included in the Actions summary and published JSON; per-row collection status is available in bond details.

`dist/` contains exactly `index.html`, `app.js`, `styles.css`, `favicon.svg`, `market.json` and `.nojekyll`. SQLite files, resumable raw state and downloaded reports are excluded. The local SQLite database keeps historical batches; the resumable JSON keeps the latest raw batch per source plus collection status. Git commits on the data branch retain earlier state revisions. That branch is visible wherever your repository's other branches are visible.

[GitHub Pages custom-workflow documentation](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages) describes the publishing setting and deployment permissions. Scheduled runs can be delayed, and public-repository schedules can be disabled after 60 days without repository activity; source dates remain visible if updates stop. [GitHub schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

### Test the runner's state handoff locally

You can exercise the same restore/collect/build pipeline without GitHub. Start with your usual local database, including any reports you have imported:

```bash
python -m bondscope build --state-output data/state.json

# Use a new, empty DB path to simulate a fresh Actions runner.
python -m bondscope --db /tmp/bondscope-runner.sqlite3 build \
  --restore-state data/state.json \
  --state-output data/next-state.json \
  --scrape --output dist
```

Omit `--scrape` for an entirely offline round trip. Restoring refuses to overwrite an existing database with source batches or collection state; choose another new `--db` path for the next simulation. State files are versioned and validated before restoration. Resumable state must stay outside the published directory. `data/state.json` and `dist/` are gitignored; choose whether to keep any differently named local state exports.

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

These optional packages are separate from the dependency-free application lockfile. Use the same Python environment to run Bonds Explore. Excel date cells and percentage-formatted XLSX/XLS cells retain their types; numeric maturity serials are not guessed from plain CSV values.

## Data model and reliability

**One row is one source observation, not necessarily one distinct bond or buyable offer.** The dashboard counts distinct instruments separately from observations. ISIN joins allow reference enrichment without collapsing different platform/source quotes. CCIL observations with no published ISIN never acquire one by name guessing.

- **Separate yield bases:** source-quoted yield, NSE last-trade annualized yield, explicitly labelled traded YTM, explicitly labelled weighted-average YTM, generic weighted-average yield, CCIL last-traded yield and Grip indicative yield remain distinct. Generic `Yield` is not promoted to YTM. Grip's indicative calculations are at face value, not market offer prices.
- **No pooled median:** when multiple bases are present, select a yield basis to see its median/range/category chart. Sorting all bases groups values by basis. These unweighted statistics describe collected observations, not the whole market or a market index.
- **Prices:** `price_inr` and `price_pct_face` are separate. A CBM/CCIL price such as `101.25` per ₹100 face is not represented as ₹101.25 per bond. Accrued-interest/clean-versus-dirty conventions are not inferred when unspecified.
- **Missing fields:** unknown ratings, prices, maturity dates and timestamps stay unknown. The original Bondbazaar's ambiguous amount strings such as `1,000,00` remain raw text. Published tenure is not used to invent a maturity date.
- **Enrichment:** reference data fills missing maturity/coupon/rating/face-value/payout fields by exact ISIN. `field_sources` records the provider and report date. Conflicting existing values remain intact and are exposed in `conflicts` and bond details. Values are not treated as independently verified merely because they appear in a reference file.
- **Status:** dated reference evidence supplies outstanding/redeemed/defaulted status. The newest available report date takes precedence; with equal dates, defaulted/redeemed evidence takes precedence. Original source status is preserved separately from status after reference checks. Maturity dates on or before the Indian calendar day mark records matured. Unknown status does not mean available for purchase.
- **Inactive/defaulted observations:** matured/redeemed rows are hidden by default but can be included using the status filter. Defaulted rows remain visible with a badge. Gold, matured, redeemed and defaulted rows are excluded from yield statistics.
- **Freshness:** collection time, report date and quote timestamp remain separate. Collection age over 24 hours is shown in bond details; it is not a guarantee of quote freshness. Unknown or old quote/report dates remain visible even after a recent collection.

The repository retains the original 28-listing, manually transcribed Bondbazaar snapshot observed on **6 November 2026**. It is labelled `snapshot`, not a successful automated scrape. This source's quote dates are unknown and its published tenures may already be outdated.

## Storage and other schedulers

The included Pages workflow schedules collection after you enable it on GitHub. No local cron job is installed. To collect every six hours on your own network-enabled machine, configure a scheduler, for example:

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
- `bondscope/__main__.py`: local HTTP API, collector/build/preview CLI.
- `bondscope/publishing.py`: static build, state export/restore and static preview.
- `scripts/pages-state.sh`: durable source-state Git branch handoff used by the Pages workflow.
- `web/`: responsive dashboard without an asset compilation step. Inter loads from Google Fonts; system fonts provide a fallback when unavailable.

```bash
python -m unittest discover -s tests -v
node --check web/app.js
node tests/dashboard.test.cjs
```

Tests cover parser schemas, price/yield separation, absent ISINs, typed XLSX data, migration, partial refreshes, report import, independent cooldowns, status chronology, reference conflicts, API behavior, source/basis filters, distinct observation details and CSV provenance. Fixtures are synthetic/reconstructed, not captured production HTML/workbooks. Legacy XLS needs verification with `xlrd` and real source files. Static publishing tests cover backend-free bundles, project subpaths, offline CLI builds, restore validation, failed-source retention and the actual state-branch script against a local bare Git remote. HTTP tests run real request/response handling through in-memory connections; JavaScript tests use a minimal DOM harness, including static-only JSON requests and operation without a refresh control.

This sandbox denies local sockets and Chromium startup, so running-server and visual browser verification could not be completed here. A live GitHub Actions deployment has not been executed from this environment. Test locally after starting the server. For background, see [RBI's government securities primer](https://www.rbi.org.in/Scripts/FAQView.aspx?Id=79). This application provides research information, not investment recommendations.
