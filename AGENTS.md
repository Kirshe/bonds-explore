# Working on Bonds Explore

## Project overview

Bonds Explore collects public Indian bond observations, imports downloaded
reports, and displays them in a dashboard. Read `README.md` for source coverage,
CLI options, collection limits, and deployment setup.

- Python 3.11+; the application and CSV/XLSX paths use the standard library.
- Legacy binary XLS support uses optional `xlrd` from `requirements-excel.txt`.
- The frontend is plain HTML, CSS, and JavaScript. There is no frontend package
  installation or asset compilation step.
- SQLite stores local source history. Static hosting serves generated JSON;
  browsers never access SQLite directly.

## Code map

| Path | Responsibility |
| --- | --- |
| `bondscope/__main__.py` | CLI, local HTTP API, CSV export |
| `bondscope/sources.py` | Source registry, provider adapters, bounded public HTTP client |
| `bondscope/scraper.py` | Original Bondbazaar parser and collector |
| `bondscope/tabular.py` | CSV/TSV, ZIP, HTML, XLSX, and optional XLS readers |
| `bondscope/models.py` | Normalized observations, stable identities, dates, yield definitions |
| `bondscope/store.py` | SQLite migration, batches, refresh/import, enrichment, market view |
| `bondscope/publishing.py` | Static build, resumable state export/restore, static preview |
| `web/` | Dashboard source assets and browser interactions |
| `data/snapshot.json` | Original labelled Bondbazaar seed snapshot |
| `tests/` | Python unittest suites, parser fixtures, Node dashboard harness |
| `.github/workflows/pages.yml` | Test, restore, collect, persist, and Pages deployment workflow |
| `scripts/pages-state.sh` | Restore/save source state through the `bondscope-data` Git branch |

## Runtime and data flow

Collectors and report imports normalize records and append successful batches
to SQLite. `read_market()` selects the latest batch for each source, enriches
matching ISINs, applies instrument status, and produces the dashboard payload.

Keep both dashboard modes working:

- **Local:** `serve` serves `web/` and exposes `GET /api/bonds`,
  `GET /api/sources`, and `POST /api/refresh`. The refresh button collects data.
- **Static:** `build` writes dashboard assets and `market.json` to `dist/`.
  The HTML mode marker selects static behavior in `web/app.js`. The browser
  fetches relative `./market.json` on page load; no refresh button is published.
  Search, filtering, sorting, pagination, details, and CSV export run locally
  in the browser. Preserve relative URLs for project Pages subpaths.

Edit `web/`, not generated `dist/`. Static output contains exactly `index.html`,
`app.js`, `styles.css`, `favicon.svg`, `market.json`, and `.nojekyll`.
Keep SQLite, raw reports, resumable state, and configuration outside it.

## Data rules to preserve

- One row is a source observation, not necessarily a distinct bond or an
  executable offer. Preserve source/instrument/yield-basis identities; do not
  collapse observations from different providers or yield bases.
- Join reference data only by exact ISIN. Keep source-scoped identities for
  ISIN-less records; never invent an ISIN from an issuer name.
- Keep yield definitions separate. Generic yield is not automatically YTM;
  indicative face-value yield is not a traded yield. Do not pool unrelated
  yield bases into a median or ranking.
- Keep rupee prices (`price_inr`) separate from prices per 100 face value
  (`price_pct_face`). Do not infer units from an ambiguous price column.
- Unknown fields stay unknown. Do not derive maturity from published tenure,
  guess ambiguous amounts, or substitute collection time for quote/report dates.
- Preserve collection method (`snapshot`, `fetched`, `imported`), collection
  time, report date, quote time, and source errors as distinct information.
  The bundled snapshot is not evidence of a successful live scrape.
- Reference enrichment fills missing fields and records provenance in
  `field_sources`; retain conflicting source values and expose `conflicts`.
  Preserve dated status precedence and maturity handling on the Indian day.
- Hide matured/redeemed rows by default; keep defaulted records identifiable.
  Exclude sovereign gold, matured, redeemed, and defaulted observations from
  conventional yield statistics. Static clients must age published data at load.
- A failed or empty collection retains that source's last-good batch. Keep
  histories, per-source errors, five-minute cooldowns, and protection against
  stale reports or an older in-flight refresh overwriting newer data.
- Preserve spreadsheet-formula neutralization in both CSV export paths, HTML
  escaping, safe source links, and local API request protections.

## Collection and publication

Use source adapters and shared readers for new report formats. Keep requests
identified, robots-aware, bounded by size/time limits, and restricted to the
provider's allowed HTTPS hosts, including redirects. Preserve request pacing.
Do not bypass login, CAPTCHA, or access controls. Dynamic empty tables and
layout drift must surface as errors rather than successful empty snapshots.

Parser fixtures are synthetic/reconstructed. Passing fixture tests does not
verify current live-provider access; report live verification separately.

The Pages workflow restores the latest raw source state into a fresh database,
collects sources, builds the public bundle, saves state to `bondscope-data`,
then deploys `dist/`. Preserve serialization of that cycle and fatal handling
of unreadable/corrupt state or failed persistence. Restore only into an empty
database; never silently reset existing state.

A plain `build` is offline. `build --scrape` may successfully publish retained
data when providers fail; `--strict` makes incomplete collection exit 1.
Fatal build/state errors exit 2. Successful publication does not establish
quote freshness.

## Local commands

Run commands from the repository root:

```bash
# Local API dashboard; does not collect automatically on startup.
python -m bondscope serve

# Offline static build and preview at http://127.0.0.1:8000.
python -m bondscope build
python -m bondscope preview

# Check project-site paths.
python -m bondscope preview --base-path /bonds-explore/ --port 8080

# Network collection, selected feeds, and report import.
python -m bondscope scrape --source nse --source grip
python -m bondscope build --scrape
python -m bondscope import-report --source nse --file /path/to/report.csv --as-of 2026-11-06

# Inspect source status or export all latest observations.
python -m bondscope sources
python -m bondscope export --output bonds.csv
```

Default storage is the gitignored `data/bonds.sqlite3`. For experiments, place
the global database option before the subcommand, for example
`python -m bondscope --db /tmp/bondscope-check.sqlite3 build --output /tmp/bondscope-site`.
Use temporary databases and outputs for verification so local data is preserved.
Do not alter the historical seed snapshot to make a test pass.

## Validation and changes

Use the existing test style: Python `unittest` and Node's built-in test runner.
No pytest, npm dependencies, or browser installation is required for these checks:

```bash
python -m unittest discover -s tests -v
node --check web/app.js
node tests/dashboard.test.cjs
```

For behavior changes, run the relevant suite; run all three checks for changes
spanning collection/storage/publication/frontend or the shared payload contract.
Add regression coverage for meaningful parser, data-integrity, API, or interaction
changes. Documentation-only changes need a content/diff review.

HTTP and dashboard tests use in-memory request/DOM harnesses. They do not replace
visual browser checks, real XLS-file checks, live-provider checks, or an actual
Pages deployment. State-branch tests use a temporary local bare Git remote.
If the environment blocks a check, state the limitation without claiming it passed.

Follow the existing small-module style and avoid adding dependencies or build
systems without a task-specific need. Update `README.md` when changing commands,
source behavior, data semantics, or publishing instructions. Summarize changed
behavior, checks performed, and any material unverified behavior when finishing.
