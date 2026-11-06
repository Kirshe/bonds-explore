import argparse
import csv
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

from .store import DEFAULT_DB, EXPORT_FIELDS, ROOT, import_report, initialize, read_market, refresh
from .sources import SOURCES


class Handler(SimpleHTTPRequestHandler):
    db_path = DEFAULT_DB

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "web"), **kwargs)

    def send_json(self, payload, status=200):
        encoded = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/bonds":
            self.send_json(read_market(self.db_path))
        elif path == "/api/sources":
            self.send_json({"sources": read_market(self.db_path)["sources"]})
        elif path.startswith("/api/"):
            self.send_json({"error": "Not found"}, 404)
        elif path in {"/", "/index.html", "/app.js", "/styles.css", "/favicon.svg"}:
            super().do_GET()
        else:
            self.send_error(404)

    def do_POST(self):
        if urlsplit(self.path).path != "/api/refresh":
            self.send_json({"error": "Not found"}, 404)
            return
        # A JSON content type and same-origin browser request keep arbitrary
        # websites from triggering local refreshes with a cross-origin form.
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self.send_json({"error": "Use application/json"}, 415)
            return
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.send_json({"error": "Cross-site requests are not allowed"}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 4096:
                raise ValueError("Request body exceeds 4096 bytes")
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object")
            ids = body.get("source_ids")
            if ids is not None and (not isinstance(ids, list) or not ids or any(not isinstance(sid, str) or sid not in SOURCES for sid in ids)):
                raise ValueError("source_ids must be a nonempty list of configured source IDs")
            result = refresh(self.db_path, source_ids=ids)
        except (ValueError, TypeError) as error:
            self.send_json({"error": str(error)}, 400)
            return
        self.send_json(result, 200 if result["ok"] else 207 if result["status"] == "partial" else 429 if result["status"] == "cooldown" else 502)


def safe_cell(value):
    # Neutralize spreadsheet formulas from externally sourced text.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def main():
    parser = argparse.ArgumentParser(description="Indian public bond listings and market dashboard")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database path")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Start the local dashboard")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    scrape = sub.add_parser("scrape", help="Fetch all configured public sources, retaining data for failed sources")
    scrape.add_argument("--source", choices=SOURCES, action="append", help="Refresh only this source; repeat to select several")
    sub.add_parser("sources", help="List source IDs, collection status, and record counts")
    report = sub.add_parser("import-report", help="Import a downloaded source report without network access")
    report.add_argument("--source", choices=SOURCES, required=True)
    report.add_argument("--file", type=Path, required=True)
    report.add_argument("--as-of", type=lambda text: date.fromisoformat(text).isoformat(), help="Report date YYYY-MM-DD; never inferred from import time")
    export = sub.add_parser("export", help="Export the latest data to CSV")
    export.add_argument("--output", type=Path, default=Path("bonds.csv"))
    args = parser.parse_args()
    initialize(args.db)
    if args.command == "scrape":
        result = refresh(args.db, source_ids=args.source)
        print(result["message"])
        if len(result["results"]) > 1:
            for item in result["results"]:
                print(f"  {item['source_id']}: {item['message']}")
        return 0 if result["ok"] else 1
    if args.command == "sources":
        for source in read_market(args.db)["sources"]:
            print(f"{source['id']:16} {source['count']:6} records  {source['mode']:10} {source['status']}")
        return 0
    if args.command == "import-report":
        try:
            print(import_report(args.db, args.source, args.file, args.as_of)["message"])
        except (ValueError, OSError) as error:
            print(f"Import failed; existing data retained. {error}", file=sys.stderr)
            return 1
        return 0
    if args.command == "export":
        market = read_market(args.db)
        bonds = market["bonds"]
        with args.output.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=EXPORT_FIELDS)
            writer.writeheader()
            for bond in bonds:
                writer.writerow({k: safe_cell(json.dumps(bond.get(k)) if isinstance(bond.get(k), (dict, list)) else bond.get(k)) for k in EXPORT_FIELDS})
        print(f"Exported {len(bonds)} listings to {args.output}")
        return 0
    handler = type("LocalHandler", (Handler,), {"db_path": args.db})
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"BondScope is running at http://{args.host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
