"""Transactional local storage. A failed refresh never replaces good data."""

from datetime import datetime, timezone
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from statistics import median

from .scraper import SOURCE, SOURCE_URL, fetch_bondbazaar, utcnow

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "bonds.sqlite3"
SNAPSHOT = ROOT / "data" / "snapshot.json"
REFRESH_SECONDS = 300


@contextmanager
def connect(path=DEFAULT_DB):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS batches (
            id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL,
            mode TEXT NOT NULL, payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS source_state (
            name TEXT PRIMARY KEY, attempted_at TEXT, status TEXT, error TEXT
        );
    """)
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize(path=DEFAULT_DB):
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM batches LIMIT 1").fetchone():
            seed = json.loads(SNAPSHOT.read_text())
            db.execute("INSERT INTO batches(observed_at, mode, payload) VALUES (?, 'snapshot', ?)",
                       (seed["observed_at"], json.dumps(seed["bonds"])))


def refresh(path=DEFAULT_DB, fetcher=fetch_bondbazaar):
    initialize(path)
    now = utcnow()
    with connect(path) as db:
        # Serialize refresh claims across concurrent server threads and CLI runs.
        db.execute("BEGIN IMMEDIATE")
        state = db.execute("SELECT * FROM source_state WHERE name=?", (SOURCE,)).fetchone()
        if state and state["attempted_at"]:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(state["attempted_at"])).total_seconds()
            if age < REFRESH_SECONDS:
                return {"ok": False, "status": "cooldown", "message": f"Please wait {max(1, int(REFRESH_SECONDS - age))} seconds before refreshing again."}
        db.execute("INSERT OR REPLACE INTO source_state VALUES (?, ?, 'fetching', NULL)", (SOURCE, now))
    try:
        bonds = fetcher()
        if not bonds:
            raise ValueError("Source returned no listings; existing data was retained")
        with connect(path) as db:
            db.execute("INSERT INTO batches(observed_at, mode, payload) VALUES (?, 'fetched', ?)",
                       (bonds[0]["observed_at"], json.dumps(bonds)))
            db.execute("UPDATE source_state SET status='ok', error=NULL WHERE name=?", (SOURCE,))
        return {"ok": True, "status": "ok", "message": f"Fetched {len(bonds)} listings from {SOURCE}."}
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        with connect(path) as db:
            db.execute("UPDATE source_state SET status='error', error=? WHERE name=?", (message, SOURCE))
        return {"ok": False, "status": "error", "message": f"Refresh failed. Existing data retained. {message}"}


def read_market(path=DEFAULT_DB):
    initialize(path)
    with connect(path) as db:
        batch = db.execute("SELECT * FROM batches ORDER BY id DESC LIMIT 1").fetchone()
        state = db.execute("SELECT * FROM source_state WHERE name=?", (SOURCE,)).fetchone()
    bonds = json.loads(batch["payload"])
    # Gold bond coupon-like figures are not comparable to conventional yields.
    conventional = [b for b in bonds if b["category"] != "Sovereign gold" and b["yield_pct"] is not None]
    yields = [b["yield_pct"] for b in conventional]
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(batch["observed_at"])).total_seconds()
    categories = []
    for name in dict.fromkeys(b["category"] for b in bonds):
        group = [b for b in bonds if b["category"] == name]
        group_yields = [b["yield_pct"] for b in group if b["yield_pct"] is not None and name != "Sovereign gold"]
        categories.append({"name": name, "count": len(group), "median_yield": median(group_yields) if group_yields else None})
    return {
        "bonds": bonds,
        "overview": {"count": len(bonds), "issuers": len({b["issuer"] for b in bonds}),
                     "median_yield": median(yields) if yields else None,
                     "min_yield": min(yields) if yields else None,
                     "max_yield": max(yields) if yields else None,
                     "yield_count": len(yields), "categories": categories},
        "source": {"name": SOURCE, "url": SOURCE_URL, "mode": batch["mode"],
                   "observed_at": batch["observed_at"], "quote_at": None,
                   "stale": age > 86400, "status": state["status"] if state else "not_refreshed",
                   "attempted_at": state["attempted_at"] if state else None,
                   "error": state["error"] if state else None},
    }
