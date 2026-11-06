"""Build a self-contained static site and persist last-good source state.

SQLite stays local to the collector. Published files contain only the dashboard
and its public JSON view; the resumable raw source state is stored separately.
"""

from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler
import json
import os
from pathlib import Path
import shutil
import tempfile
from urllib.parse import unquote, urlsplit

from .models import normalize_legacy
from .scraper import utcnow
from .sources import SOURCES
from .store import DEFAULT_DB, ROOT, connect, initialize, read_market, refresh

STATIC_FILES = {"index.html", "app.js", "styles.css", "favicon.svg", "market.json", ".nojekyll"}
MAX_STATE_BYTES = 100 * 1024 * 1024


def write_json(path, data):
    """Replace one JSON file atomically; never leave a truncated state file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".bondscope-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            file.write("\n")
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_state(db_path, output):
    initialize(db_path)
    with connect(db_path) as db:
        batches = [{"source_id": row["source_id"], "observed_at": row["observed_at"],
                    "mode": row["mode"], "records": json.loads(row["payload"])}
                   for row in db.execute("SELECT * FROM batches WHERE id IN (SELECT MAX(id) FROM batches GROUP BY source_id) ORDER BY source_id")]
        states = [{k: row[k] for k in ("name", "attempted_at", "status", "error")}
                  for row in db.execute("SELECT * FROM source_state ORDER BY name")]
    state = {"schema_version": 1, "saved_at": utcnow(), "batches": batches, "source_states": states}
    if len(json.dumps(state, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")) + 1 > MAX_STATE_BYTES:
        raise ValueError("Resumable source state exceeds 100 MB")
    write_json(output, state)
    return state


def _timestamp(value, *, optional=False):
    if optional and value is None:
        return
    if not isinstance(value, str) or datetime.fromisoformat(value).tzinfo is None:
        raise ValueError("Source state timestamps must include a timezone")


def restore_state(db_path, input_path):
    """Restore into an empty DB only; reject corrupt/unsupported state first."""
    input_path = Path(input_path)
    if input_path.stat().st_size > MAX_STATE_BYTES:
        raise ValueError("Source state exceeds 100 MB")
    state = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise ValueError("Unsupported source state schema")
    batches, states = state.get("batches"), state.get("source_states")
    if not isinstance(batches, list) or not batches or not isinstance(states, list):
        raise ValueError("Source state must contain batches and source_states")
    seen = set()
    try:
        for batch in batches:
            sid = batch["source_id"]
            if sid not in SOURCES or sid in seen:
                raise ValueError("Unknown or duplicated source in saved state")
            seen.add(sid)
            _timestamp(batch["observed_at"])
            if batch["mode"] not in {"snapshot", "fetched", "imported"}:
                raise ValueError("Invalid source collection mode")
            if not isinstance(batch["records"], list) or not batch["records"]:
                raise ValueError("Saved source batches must contain records")
            for row in batch["records"]:
                normalized = normalize_legacy(row)
                if normalized["source_id"] != sid:
                    raise ValueError("Saved record belongs to a different source")
                _timestamp(normalized["observed_at"])
        seen_states = set()
        for source in states:
            if source["name"] not in SOURCES or source["name"] in seen_states:
                raise ValueError("Unknown or duplicated source state")
            seen_states.add(source["name"])
            _timestamp(source["attempted_at"], optional=True)
            if source["status"] not in {"ok", "error", "fetching", "imported", "not_refreshed"}:
                raise ValueError("Invalid saved collection status")
            if source["error"] is not None and not isinstance(source["error"], str):
                raise ValueError("Invalid saved source error")
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("Incomplete or invalid source state") from error
    with connect(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM batches LIMIT 1").fetchone() or db.execute("SELECT 1 FROM source_state LIMIT 1").fetchone():
            raise ValueError("Restore requires an empty database; choose a new --db path to preserve local data")
        for batch in batches:
            db.execute("INSERT INTO batches(source_id, observed_at, mode, payload) VALUES (?, ?, ?, ?)",
                       (batch["source_id"], batch["observed_at"], batch["mode"], json.dumps(batch["records"], allow_nan=False)))
        for source in states:
            interrupted = source["status"] == "fetching"
            db.execute("INSERT INTO source_state(name, attempted_at, status, error) VALUES (?, ?, ?, ?)",
                       (source["name"], source["attempted_at"], "error" if interrupted else source["status"],
                        "Previous collection was interrupted; existing data retained" if interrupted else source["error"]))


def _check_output(output, db_path, state_output, state_input):
    output = Path(output).resolve()
    web = (ROOT / "web").resolve()
    if output == web or output.is_relative_to(web) or web.is_relative_to(output):
        raise ValueError("Build output must be separate from the source checkout and web assets")
    for private in (db_path, state_output, state_input):
        if private is not None and Path(private).resolve().is_relative_to(output):
            raise ValueError("Database and resumable state must stay outside the published directory")
    if output.exists():
        if not output.is_dir():
            raise ValueError("Build output must be a directory")
        unexpected = [p.name for p in output.iterdir() if p.name not in STATIC_FILES or p.is_symlink() or not p.is_file()]
        if unexpected:
            raise ValueError("Build output contains unrelated files; use an empty directory: " + ", ".join(sorted(unexpected)))
    return output


def build_site(db_path=DEFAULT_DB, output="dist", *, scrape=False, source_ids=None,
               state_input=None, state_output=None):
    output = _check_output(output, db_path, state_output, state_input)
    if state_output is not None and Path(state_output).resolve() == Path(db_path).resolve():
        raise ValueError("State output cannot replace the SQLite database")
    if source_ids and not scrape:
        raise ValueError("--source requires --scrape")
    if state_input is not None:
        restore_state(db_path, state_input)
    initialize(db_path)
    collection = refresh(db_path, source_ids=source_ids) if scrape else None
    market = read_market(db_path)
    market["publication"] = {"schema_version": 1, "mode": "static", "generated_at": utcnow(),
                             "collection_status": collection["status"] if collection else "not_requested"}
    # Validate the full JSON before touching any previously built files.
    json.dumps(market, allow_nan=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".bondscope-build-", dir=output.parent) as staging:
        stage = Path(staging)
        for name in ("app.js", "styles.css", "favicon.svg"):
            shutil.copyfile(ROOT / "web" / name, stage / name)
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        if 'data-mode="live"' not in html:
            raise ValueError("Dashboard template has no runtime mode marker")
        html = html.replace('data-mode="live"', 'data-mode="static"', 1)
        (stage / "index.html").write_text(html, encoding="utf-8")
        (stage / ".nojekyll").write_text("", encoding="utf-8")
        write_json(stage / "market.json", market)
        if state_output is not None:
            save_state(db_path, state_output)
        output.mkdir(parents=True, exist_ok=True)
        # Only the allowlisted public files are copied; no DB, state, source
        # reports, Git metadata or secret configuration enters the Pages bundle.
        for name in sorted(STATIC_FILES):
            os.replace(stage / name, output / name)
    return {"output": str(output), "observations": len(market["bonds"]), "collection": collection,
            "generated_at": market["publication"]["generated_at"]}


def base_path(value):
    if not isinstance(value, str) or not value.startswith("/") or "?" in value or "#" in value or "\\" in value:
        raise ValueError("Base path must be an absolute URL path such as /bondscrapper/")
    decoded = unquote(value)
    if "//" in decoded or any(part in {".", ".."} for part in decoded.split("/")):
        raise ValueError("Base path cannot contain traversal or duplicate slashes")
    return value.rstrip("/") + "/" if value != "/" else "/"


class StaticHandler(SimpleHTTPRequestHandler):
    """Preview exactly the static Pages bundle, optionally under /repo/."""

    mount_path = "/"

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def _dispatch(self, head=False):
        path = urlsplit(self.path).path
        if self.mount_path != "/" and path == self.mount_path.rstrip("/"):
            self.send_response(301)
            self.send_header("Location", self.mount_path)
            self.end_headers()
            return
        if not path.startswith(self.mount_path):
            self.send_error(404)
            return
        relative = unquote(path[len(self.mount_path):])
        if relative not in STATIC_FILES | {""}:
            self.send_error(404)
            return
        self.path = "/" + (relative or "index.html")
        if head:
            super().do_HEAD()
        else:
            super().do_GET()

    def do_GET(self):
        self._dispatch()

    def do_HEAD(self):
        self._dispatch(head=True)


def preview_handler(directory, mount="/"):
    directory = Path(directory).resolve()
    if not (directory / "market.json").is_file() or not (directory / "index.html").is_file():
        raise ValueError("No static build found; run python -m bondscope build first")
    handler = type("MountedStaticHandler", (StaticHandler,), {"mount_path": base_path(mount)})
    return partial(handler, directory=str(directory))
