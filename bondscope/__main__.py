import argparse
import csv
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

from .store import DEFAULT_DB, ROOT, initialize, read_market, refresh


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
        result = refresh(self.db_path)
        self.send_json(result, 200 if result["ok"] else (429 if result["status"] == "cooldown" else 502))


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
    sub.add_parser("scrape", help="Fetch public bond listings once")
    export = sub.add_parser("export", help="Export the latest data to CSV")
    export.add_argument("--output", type=Path, default=Path("bonds.csv"))
    args = parser.parse_args()
    initialize(args.db)
    if args.command == "scrape":
        result = refresh(args.db)
        print(result["message"])
        return 0 if result["ok"] else 1
    if args.command == "export":
        market = read_market(args.db)
        bonds = market["bonds"]
        fields = list(bonds[0]) + ["data_mode", "source_status", "source_stale"]
        with args.output.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for bond in bonds:
                row = {**bond, "data_mode": market["source"]["mode"], "source_status": market["source"]["status"], "source_stale": market["source"]["stale"]}
                writer.writerow({k: safe_cell(v) for k, v in row.items()})
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
