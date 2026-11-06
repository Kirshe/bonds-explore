"""Independent source histories, safe refreshes, and ISIN-based enrichment."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from statistics import median
from uuid import uuid4

from .models import YIELD_LABELS, effective_status, normalize_legacy
from .scraper import utcnow
from .sources import SOURCES
from .tabular import MAX_BYTES

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "bonds.sqlite3"
SNAPSHOT = ROOT / "data" / "snapshot.json"
REFRESH_SECONDS = 300
EXPORT_FIELDS = [
    "id", "instrument_id", "isin", "issuer", "category", "yield_pct", "yield_kind", "yield_label",
    "coupon_pct", "rating", "price_inr", "price_pct_face", "price_basis", "face_value_inr",
    "min_investment_inr", "min_investment_raw", "payout", "tenure_raw", "maturity_date",
    "instrument_status", "source_instrument_status", "source_id", "source", "source_url", "observed_at", "quote_at",
    "report_as_of", "data_mode", "source_status", "source_stale", "field_sources", "conflicts",
    "bid_yield_pct", "offer_yield_pct", "bid_price_pct_face", "offer_price_pct_face",
    "trade_count", "traded_value_raw", "availability", "import_filename",
]


@contextmanager
def connect(path=DEFAULT_DB):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        # Migrate the original single-source database without dropping history.
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS batches (id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, mode TEXT NOT NULL, payload TEXT NOT NULL, source_id TEXT NOT NULL DEFAULT 'bondbazaar')")
        if "source_id" not in {r[1] for r in db.execute("PRAGMA table_info(batches)")}:
            db.execute("ALTER TABLE batches ADD COLUMN source_id TEXT NOT NULL DEFAULT 'bondbazaar'")
        db.execute("CREATE TABLE IF NOT EXISTS source_state (name TEXT PRIMARY KEY, attempted_at TEXT, status TEXT, error TEXT, attempt_id TEXT)")
        if "attempt_id" not in {r[1] for r in db.execute("PRAGMA table_info(source_state)")}:
            db.execute("ALTER TABLE source_state ADD COLUMN attempt_id TEXT")
        db.execute("UPDATE OR IGNORE source_state SET name='bondbazaar' WHERE name='Bondbazaar'")
        db.execute("CREATE INDEX IF NOT EXISTS batches_source ON batches(source_id, id)")
        db.commit()
        with db:
            yield db
    finally:
        db.close()


def initialize(path=DEFAULT_DB):
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM batches WHERE source_id='bondbazaar' LIMIT 1").fetchone():
            seed = json.loads(SNAPSHOT.read_text())
            db.execute("INSERT INTO batches(observed_at, mode, payload, source_id) VALUES (?, 'snapshot', ?, 'bondbazaar')",
                       (seed["observed_at"], json.dumps(seed["bonds"])))


def _save(path, source_id, bonds, mode, token=None):
    if not bonds:
        raise ValueError("Source returned no records; existing data retained")
    normalized = []
    for row in bonds:
        bond = normalize_legacy(row)
        if bond["source_id"] != source_id:
            # Legacy Bondbazaar adapter has no source_id; every new adapter does.
            raise ValueError("Adapter returned a record belonging to another source")
        normalized.append(bond)
    # Duplicate responsive HTML rows are collapsed within a source/basis only.
    unique = {bond["id"]: bond for bond in normalized}
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        if token:
            state = db.execute("SELECT attempt_id FROM source_state WHERE name=?", (source_id,)).fetchone()
            if not state or state["attempt_id"] != token:
                raise ValueError("A newer source update superseded this refresh")
        previous = db.execute("SELECT payload FROM batches WHERE source_id=? ORDER BY id DESC LIMIT 1", (source_id,)).fetchone()
        dated = lambda rows: max((str(b.get("report_as_of") or b.get("quote_at") or "")[:10] for b in rows), default="")
        if previous:
            previous_date = dated(json.loads(previous["payload"]))
            incoming_date = dated(unique.values())
            if previous_date and incoming_date < previous_date:
                raise ValueError(f"Report date {incoming_date or 'unknown'} is older than the stored {previous_date} report; existing data retained. For an undated download, supply its printed date with --as-of.")
        db.execute("INSERT INTO batches(observed_at, mode, payload, source_id) VALUES (?, ?, ?, ?)",
                   (utcnow(), mode, json.dumps(list(unique.values())), source_id))
        status = "imported" if mode == "imported" else "ok"
        db.execute("INSERT INTO source_state(name, attempted_at, status, error, attempt_id) VALUES (?, ?, ?, NULL, ?) ON CONFLICT(name) DO UPDATE SET status=excluded.status, error=NULL, attempt_id=excluded.attempt_id",
                   (source_id, None if mode == "imported" else utcnow(), status, token or uuid4().hex))
    return len(unique)


def _refresh_one(path, source_id, fetcher):
    now, token = utcnow(), uuid4().hex
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        state = db.execute("SELECT * FROM source_state WHERE name=?", (source_id,)).fetchone()
        if state and state["attempted_at"]:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(state["attempted_at"])).total_seconds()
            if age < REFRESH_SECONDS:
                return {"source_id": source_id, "ok": False, "status": "cooldown", "message": f"Please wait {max(1, int(REFRESH_SECONDS - age))} seconds before refreshing {SOURCES[source_id].name} again."}
        db.execute("INSERT OR REPLACE INTO source_state(name, attempted_at, status, error, attempt_id) VALUES (?, ?, 'fetching', NULL, ?)", (source_id, now, token))
    try:
        count = _save(path, source_id, fetcher(), "fetched", token)
        return {"source_id": source_id, "ok": True, "status": "ok", "message": f"Fetched {count} records from {SOURCES[source_id].name}."}
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        with connect(path) as db:
            db.execute("UPDATE source_state SET status='error', error=? WHERE name=? AND attempt_id=?", (message, source_id, token))
        return {"source_id": source_id, "ok": False, "status": "error", "message": f"Refresh failed for {SOURCES[source_id].name}. Existing data retained. {message}"}


def refresh(path=DEFAULT_DB, fetcher=None, source_ids=None):
    initialize(path)
    ids = list(dict.fromkeys(source_ids or (["bondbazaar"] if fetcher else SOURCES)))
    unknown = set(ids) - SOURCES.keys()
    if unknown:
        raise ValueError("Unknown source: " + ", ".join(sorted(unknown)))
    results = [_refresh_one(path, sid, fetcher or SOURCES[sid].fetch) for sid in ids]
    if len(results) == 1:
        return {**results[0], "results": results}
    successes = sum(r["ok"] for r in results)
    status = "ok" if successes == len(results) else "partial" if successes else "cooldown" if all(r["status"] == "cooldown" for r in results) else "error"
    return {"ok": successes == len(results), "status": status, "message": f"{successes}/{len(results)} sources refreshed. Existing data retained for sources that did not refresh.", "results": results}


def import_report(path, source_id, report_path, as_of=None):
    if source_id not in SOURCES:
        raise ValueError(f"Unknown source: {source_id}")
    initialize(path)
    report_path = Path(report_path)
    if report_path.stat().st_size > MAX_BYTES:
        raise ValueError("Report exceeds 20 MB")
    bonds = SOURCES[source_id].parse(report_path.read_bytes(), report_path.name, as_of)
    imported_at = utcnow()
    for bond in bonds:
        bond["observed_at"] = imported_at
        # Store the local collection method without claiming a network fetch.
        bond["import_filename"] = report_path.name
    count = _save(path, source_id, bonds, "imported")
    return {"ok": True, "status": "ok", "message": f"Imported {count} records for {SOURCES[source_id].name}."}


def _source_info(source_id, batch, state):
    source = SOURCES[source_id]
    stamp = batch["observed_at"] if batch else None
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds() if stamp else None
    return {"id": source_id, "name": source.name, "url": source.url, "role": source.role, "note": source.note,
            "mode": batch["mode"] if batch else "empty", "observed_at": stamp, "quote_at": None,
            "stale": age is None or age > 86400, "status": state["status"] if state else "not_refreshed",
            "attempted_at": state["attempted_at"] if state else None, "error": state["error"] if state else None,
            "count": len(json.loads(batch["payload"])) if batch else 0}


def _enrich(bonds):
    by_isin = {}
    for bond in bonds:
        if bond.get("isin") and bond["yield_kind"] == "reference":
            by_isin.setdefault(bond["isin"], []).append(bond)
    for bond in bonds:
        provenance, conflicts = {}, []
        bond["source_instrument_status"] = bond["instrument_status"]
        references = sorted(by_isin.get(bond.get("isin"), []), key=lambda b: (b.get("report_as_of") or "", b["observed_at"]), reverse=True)
        for field in ("maturity_date", "coupon_pct", "rating", "face_value_inr", "payout"):
            candidates = [r for r in references if r.get(field) is not None and r.get(field) != ""]
            if not candidates:
                continue
            chosen = candidates[0]
            evidence = {"source_id": chosen["source_id"], "observed_at": chosen["observed_at"], "report_as_of": chosen.get("report_as_of")}
            if bond.get(field) is None:
                bond[field] = chosen[field]
                provenance[field] = evidence
            elif chosen[field] != bond[field] and chosen["source_id"] != bond["source_id"]:
                conflicts.append({"field": field, "value": bond[field], "reference_value": chosen[field], **evidence})
        statuses = [r for r in references if r["instrument_status"] != "unknown"]
        if statuses:
            newest_date = max(r.get("report_as_of") or "" for r in statuses)
            eligible = [r for r in statuses if (r.get("report_as_of") or "") == newest_date]
            chosen = max(eligible, key=lambda r: {"defaulted": 3, "redeemed": 2, "outstanding": 1}.get(r["instrument_status"], 0))
            if bond["instrument_status"] == "unknown" or (chosen.get("report_as_of") or "") >= (bond.get("report_as_of") or ""):
                bond["instrument_status"] = chosen["instrument_status"]
                provenance["instrument_status"] = {"source_id": chosen["source_id"], "report_as_of": chosen.get("report_as_of"), "observed_at": chosen["observed_at"]}
        bond["instrument_status"] = effective_status(bond)
        bond["field_sources"], bond["conflicts"] = provenance, conflicts
    return bonds


def _stats(bonds):
    yields = [b["yield_pct"] for b in bonds if b["yield_pct"] is not None]
    categories = []
    for name in dict.fromkeys(b["category"] for b in bonds):
        values = [b["yield_pct"] for b in bonds if b["category"] == name and b["yield_pct"] is not None]
        categories.append({"name": name, "count": sum(b["category"] == name for b in bonds), "median_yield": median(values) if values else None})
    return {"median_yield": median(yields) if yields else None, "min_yield": min(yields) if yields else None,
            "max_yield": max(yields) if yields else None, "yield_count": len(yields), "categories": categories}


def read_market(path=DEFAULT_DB):
    initialize(path)
    with connect(path) as db:
        batches = {row["source_id"]: row for row in db.execute("SELECT * FROM batches WHERE id IN (SELECT MAX(id) FROM batches GROUP BY source_id)")}
        states = {row["name"]: row for row in db.execute("SELECT * FROM source_state")}
    sources = [_source_info(sid, batches.get(sid), states.get(sid)) for sid in SOURCES]
    source_map = {s["id"]: s for s in sources}
    bonds = []
    # A restored database may insert batches in a different order. Keep source
    # ordering (and equal-date reference precedence) independent of SQLite IDs.
    for sid in SOURCES:
        batch = batches.get(sid)
        if batch is None:
            continue
        source = source_map[sid]
        for raw in json.loads(batch["payload"]):
            row = normalize_legacy(raw)
            row.update(data_mode=source["mode"], source_status=source["status"], source_stale=source["stale"])
            bonds.append(row)
    bonds = _enrich(bonds)
    conventional = [b for b in bonds if b["category"] != "Sovereign gold" and b["instrument_status"] not in {"matured", "redeemed", "defaulted"}]
    groups = [{"kind": kind, "label": label, **_stats([b for b in conventional if b["yield_kind"] == kind])} for kind, label in YIELD_LABELS.items() if kind != "reference"]
    # Compatibility statistics explicitly describe only the quoted-yield basis.
    quoted = next(g for g in groups if g["kind"] == "quoted")
    categories = [{"name": name, "count": len({b["instrument_id"] for b in bonds if b["category"] == name})} for name in dict.fromkeys(b["category"] for b in bonds)]
    return {"bonds": bonds, "sources": sources, "export_fields": EXPORT_FIELDS,
            "overview": {"count": len({b["instrument_id"] for b in bonds}), "observation_count": len(bonds),
                         "categories": categories, "yield_groups": groups,
                         **{k: quoted[k] for k in ("median_yield", "min_yield", "max_yield", "yield_count")}},
            "source": source_map["bondbazaar"]}
