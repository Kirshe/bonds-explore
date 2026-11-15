import csv
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from bondscope.__main__ import Handler
from bondscope.scraper import fetch_bondbazaar, money, parse_bondbazaar
from bondscope.store import read_market, refresh

FIXTURE = (Path(__file__).parent / "fixtures" / "bondbazaar.html").read_text()
STAMP = "2026-11-06T11:40:17+00:00"


class ParserTests(unittest.TestCase):
    def test_fields_categories_and_duplicates(self):
        bonds = parse_bondbazaar(FIXTURE, STAMP)
        self.assertEqual(len(bonds), 3)
        corporate = next(b for b in bonds if b["isin"] == "INE03K307215")
        self.assertEqual(corporate["issuer"], "Satin Finserv Limited")
        self.assertEqual(corporate["category"], "Corporate / NBFC")
        self.assertEqual(corporate["yield_pct"], 11.95)
        self.assertEqual(corporate["coupon_pct"], 10.8)
        self.assertEqual(corporate["payout"], "Quarterly")
        self.assertEqual(corporate["sector"], "Financial Services")
        self.assertEqual(corporate["tenure_raw"], "2Yrs 02Mon")
        self.assertEqual(corporate["min_investment_raw"], "100,000,00")
        self.assertIsNone(corporate["min_investment_inr"])
        self.assertIsNone(corporate["maturity_date"])
        self.assertIsNone(corporate["rating"])
        self.assertEqual(corporate["observed_at"], STAMP)
        self.assertEqual({b["category"] for b in bonds}, {"State government", "Sovereign gold", "Corporate / NBFC"})

    def test_visible_card_without_compact_duplicate(self):
        html = '<h3>PSU Bonds Available Bonds</h3><h4>REC Limited</h4><p>ISIN: INE020B07GX4</p><div>Interest Payment: Annual</div><div>Yield: 5.50</div><div>Min Invest<h6>1,000.00</h6></div><div>Coupon Rate<h6>7.38</h6></div><div>Tenure<h6>1Yrs 02Mon</h6></div>'
        bond = parse_bondbazaar(html)[0]
        self.assertEqual(bond["yield_pct"], 5.5)
        self.assertEqual(bond["coupon_pct"], 7.38)
        self.assertEqual(bond["min_investment_inr"], 1000)
        self.assertEqual(bond["payout"], "Annual")

    def test_layout_drift_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "layout may have changed"):
            parse_bondbazaar("<html><h1>Please sign in</h1></html>")

    def test_ambiguous_amounts_are_not_guessed(self):
        for value in ["1,000,00", "100,000,00", "750,00", "NA", "-10"]:
            self.assertIsNone(money(value))
        for value, expected in [("1,00,000", 100000), ("100,000.00", 100000), ("₹1,000", 1000), ("5000", 5000)]:
            self.assertEqual(money(value), expected)

    @patch("bondscope.scraper._get")
    def test_robots_disallow_prevents_page_fetch(self, get):
        get.return_value = "User-agent: *\nDisallow: /"
        with self.assertRaisesRegex(RuntimeError, "disallows"):
            fetch_bondbazaar()
        self.assertEqual(get.call_count, 1)

    @patch("bondscope.scraper._get")
    def test_missing_robots_allows_public_page(self, get):
        get.side_effect = [HTTPError("robots", 404, "Not found", {}, None), FIXTURE]
        self.assertEqual(len(fetch_bondbazaar()), 3)

    @patch("bondscope.scraper._get")
    def test_robots_access_failure_is_not_bypassed(self, get):
        get.side_effect = HTTPError("robots", 403, "Forbidden", {}, None)
        with self.assertRaisesRegex(RuntimeError, "Cannot verify"):
            fetch_bondbazaar()
        self.assertEqual(get.call_count, 1)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "test.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def test_snapshot_provenance_and_gold_exclusion(self):
        data = read_market(self.db)
        self.assertEqual(data["source"]["mode"], "snapshot")
        self.assertIsNone(data["source"]["quote_at"])
        self.assertEqual(data["overview"]["count"], 28)
        self.assertEqual(data["overview"]["yield_count"], 23)
        self.assertEqual(data["overview"]["min_yield"], 5.5)

    def test_failed_refresh_preserves_data_and_timestamp(self):
        before = read_market(self.db)
        def fail():
            raise OSError("Network unavailable")
        result = refresh(self.db, fail)
        after = read_market(self.db)
        self.assertFalse(result["ok"])
        # Collection status changes; the stored observations do not.
        strip_status = lambda rows: [{k: v for k, v in row.items() if k != "source_status"} for row in rows]
        self.assertEqual(strip_status(before["bonds"]), strip_status(after["bonds"]))
        self.assertEqual(before["source"]["observed_at"], after["source"]["observed_at"])
        self.assertEqual(after["source"]["status"], "error")
        self.assertIn("Network unavailable", after["source"]["error"])

    def test_success_replaces_latest_but_keeps_history(self):
        result = refresh(self.db, lambda: parse_bondbazaar(FIXTURE, STAMP))
        data = read_market(self.db)
        self.assertTrue(result["ok"])
        self.assertEqual(data["source"]["mode"], "fetched")
        self.assertEqual(data["overview"]["count"], 3)
        self.assertEqual(data["overview"]["yield_count"], 2)
        self.assertEqual(data["overview"]["median_yield"], 9.975)
        from bondscope.store import connect
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM batches").fetchone()[0], 2)

    def test_cooldown_blocks_duplicate_fetches(self):
        refresh(self.db, lambda: parse_bondbazaar(FIXTURE, STAMP))
        with patch("bondscope.scraper.fetch_bondbazaar") as fetch:
            result = refresh(self.db, fetch)
            self.assertEqual(result["status"], "cooldown")
            fetch.assert_not_called()

    def test_empty_refresh_is_not_success(self):
        result = refresh(self.db, lambda: [])
        self.assertFalse(result["ok"])
        self.assertEqual(read_market(self.db)["overview"]["count"], 28)

    def test_csv_contains_provenance_and_raw_amount(self):
        output = Path(self.temp.name) / "bonds.csv"
        subprocess.run([sys.executable, "-m", "bondscope", "--db", str(self.db), "export", "--output", str(output)], check=True, capture_output=True)
        with output.open(encoding="utf-8-sig") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(len(rows), 28)
        self.assertEqual(rows[0]["data_mode"], "snapshot")
        self.assertEqual(rows[0]["min_investment_raw"], "10,000,00")
        self.assertEqual(rows[0]["min_investment_inr"], "")


class ServerTests(unittest.TestCase):
    """Exercise actual HTTP parsing/serialization without binding a socket."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.handler = type("TestHandler", (Handler,), {"db_path": Path(self.temp.name) / "server.sqlite3", "log_message": lambda *args: None})

    def tearDown(self):
        self.temp.cleanup()

    def request(self, method, path, headers=None):
        headers = {"Host": "127.0.0.1:8000", "Connection": "close", **(headers or {})}
        raw = f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n"
        output = BytesIO()
        connection = SimpleNamespace(makefile=lambda *args: BytesIO(raw.encode()), sendall=output.write)
        server = SimpleNamespace(server_name="localhost", server_port=8000)
        self.handler(connection, ("127.0.0.1", 12345), server)
        head, _, body = output.getvalue().partition(b"\r\n\r\n")
        self.response_headers = head.decode("iso-8859-1")
        return int(head.split(b" ")[1]), body

    def test_page_assets_and_api(self):
        for path in ["/", "/styles.css", "/app.js", "/favicon.svg"]:
            status, data = self.request("GET", path)
            self.assertEqual(status, 200, path)
            self.assertTrue(data)
        status, data = self.request("GET", "/api/bonds")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(data)["bonds"]), 28)

    def test_versioned_assets_and_html_are_not_cached(self):
        for path in ["/", "/app.js?v=4", "/styles.css?v=2", "/favicon.svg?v=2", "/api/bonds"]:
            status, data = self.request("GET", path)
            self.assertEqual(status, 200, path)
            self.assertTrue(data)
            self.assertIn("Cache-Control: no-store", self.response_headers)
        _, html = self.request("GET", "/")
        self.assertIn(b'./app.js?v=4', html)
        self.assertIn(b'./styles.css?v=2', html)

    def test_private_files_are_not_served(self):
        for path in ["/data/bonds.sqlite3", "/../README.md", "/api/no-such-route"]:
            self.assertEqual(self.request("GET", path)[0], 404)

    def test_cross_site_refresh_rejected(self):
        self.assertEqual(self.request("POST", "/api/refresh")[0], 415)
        self.assertEqual(self.request("POST", "/api/refresh", {"Content-Type": "application/json", "Sec-Fetch-Site": "cross-site"})[0], 403)

    @patch("bondscope.__main__.refresh")
    def test_refresh_http_status_and_error_message(self, fetch):
        fetch.return_value = {"ok": False, "status": "error", "message": "Network unavailable. Existing data retained."}
        status, data = self.request("POST", "/api/refresh", {"Content-Type": "application/json"})
        self.assertEqual(status, 502)
        self.assertIn("retained", json.loads(data)["message"])
        fetch.return_value = {"ok": False, "status": "cooldown", "message": "Please wait"}
        self.assertEqual(self.request("POST", "/api/refresh", {"Content-Type": "application/json"})[0], 429)


if __name__ == "__main__":
    unittest.main()
