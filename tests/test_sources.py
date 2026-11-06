from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from bondscope.models import effective_status, numeric, observation
from bondscope.sources import (SOURCES, PublicClient, _fetch_nsdl, parse_ccil, parse_grip,
                               parse_nse, parse_reference, report_date)
from bondscope.store import connect, import_report, read_market, refresh
from bondscope.tabular import read_tables

FIXTURES = Path(__file__).parent / "fixtures"


class AdapterTests(unittest.TestCase):
    def test_nse_keeps_yield_bases_and_price_units_distinct(self):
        rows = parse_nse((FIXTURES / "nse.csv").read_bytes())
        self.assertEqual({r["yield_kind"] for r in rows}, {"traded_annualized", "weighted_ytm"})
        last = next(r for r in rows if r["yield_kind"] == "traded_annualized")
        weighted = next(r for r in rows if r["yield_kind"] == "weighted_ytm")
        self.assertEqual(last["yield_pct"], 10.25)
        self.assertEqual(weighted["yield_pct"], 10.30)
        self.assertEqual(last["price_pct_face"], 101.25)
        self.assertIsNone(last["price_inr"])
        self.assertEqual(last["quote_at"], "2026-11-06")
        self.assertEqual(last["maturity_date"], "2029-11-02")
        self.assertNotEqual(last["id"], weighted["id"])

    def test_generic_yield_and_price_are_not_misclassified(self):
        data = b"ISIN,Issuer,Yield,Price\nINE03K307215,Example,10.5,100.5\n"
        row = parse_nse(data)[0]
        self.assertEqual(row["yield_kind"], "reference")
        self.assertIsNone(row["yield_pct"])
        self.assertIsNone(row["price_inr"])
        self.assertIsNone(row["price_pct_face"])
        self.assertIsNone(row["quote_at"])
        cm = parse_nse(b"ISIN,Issuer,LTP,FaceValue\nINE03K307215,Example,1006.74,1000\n")[0]
        self.assertIsNone(cm["price_pct_face"])

    def test_nsdl_reference_fields_and_filename_dates(self):
        rows = parse_reference((FIXTURES / "nsdl.csv").read_bytes(), "Debt_Instruments_as_on_04.11.2026.csv")
        self.assertEqual(rows[0]["report_as_of"], "2026-11-04")
        self.assertEqual(rows[0]["face_value_inr"], 100000)
        self.assertEqual(rows[0]["instrument_status"], "outstanding")
        self.assertEqual(rows[0]["rating"], "A-")
        self.assertIsNone(rows[0]["yield_pct"])
        self.assertIsNone(rows[0]["quote_at"])

    def test_report_title_with_isin_word_is_not_a_column_header(self):
        data = b"List of ISINs defaulted in redemption\nISIN;Issuer Name;Status\nINE03K307215;Example;Defaulted\n"
        row = parse_reference(data)[0]
        self.assertEqual(row["issuer"], "Example")
        self.assertEqual(row["instrument_status"], "defaulted")

    def test_default_and_redeemed_lists_have_explicit_separate_status(self):
        data = b"ISIN\nINE03K307215\n"
        defaults = SOURCES["nsdl-defaults"].parse(data, "defaults.csv", "2026-11-05")
        redeemed = SOURCES["nsdl-redeemed"].parse(data, "redeemed.csv", "2026-11-04")
        self.assertEqual(defaults[0]["instrument_status"], "defaulted")
        self.assertEqual(redeemed[0]["instrument_status"], "redeemed")
        self.assertNotEqual(defaults[0]["id"], redeemed[0]["id"])
        self.assertEqual(defaults[0]["source_id"], "nsdl-defaults")

    def test_ccil_does_not_invent_isin_or_rupee_price(self):
        rows = parse_ccil((FIXTURES / "ccil.html").read_bytes(), as_of="2026-11-06")
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0]["isin"])
        self.assertTrue(rows[0]["instrument_id"].startswith("ccil:"))
        self.assertEqual(rows[0]["category"], "Central government")
        self.assertEqual(rows[0]["yield_kind"], "ccil_traded")
        self.assertEqual(rows[1]["category"], "State government")
        self.assertEqual(rows[0]["bid_yield_pct"], 6.9)
        self.assertEqual(rows[0]["price_pct_face"], 100.3)
        self.assertIsNone(rows[0]["price_inr"])

    def test_empty_dynamic_tables_are_errors(self):
        for parser in [parse_ccil, parse_grip, parse_nse, parse_reference]:
            with self.subTest(parser=parser.__name__), self.assertRaises(ValueError):
                parser(b"<html><table><tr><th>ISIN</th></tr></table></html>")

    def test_grip_indicative_yield_clean_name_and_deduplication(self):
        rows = parse_grip((FIXTURES / "grip.html").read_bytes())
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["issuer"], "Example Finance")
        self.assertEqual(rows[0]["rating"], "A-")
        self.assertEqual(rows[0]["payout"], "Quarterly")
        self.assertEqual(rows[0]["yield_kind"], "indicative")
        self.assertEqual(rows[0]["maturity_date"], "2029-11-02")
        self.assertIsNone(rows[0]["quote_at"])
        self.assertIsNone(rows[0]["price_inr"])
        self.assertIn("INE03K307215", rows[0]["source_url"])

    def test_dates_are_read_from_report_not_observation_clock(self):
        self.assertEqual(report_date("Corporate_bond_report_06-Nov-2026.csv"), "2026-11-06")
        self.assertEqual(report_date("cbm_06112026.csv"), "2026-11-06")
        self.assertIsNone(report_date("report.csv"))

    def test_matured_records_and_invalid_numbers(self):
        self.assertEqual(effective_status({"maturity_date": "2020-01-01"}), "matured")
        for value in ["NaN", "inf", "-1", "not published", "1,000,00"]:
            self.assertIsNone(numeric(value))
        self.assertIsNone(numeric("1000", percent=True))

    def test_source_link_discovery_uses_published_nsdl_filename(self):
        link = "https://nsdl.com/nsdl/2026-11/Download_the_entire_list_of_Debt_Instruments_%28including_Redeemed%29_as_on_04.11.2026.xls"
        with patch("bondscope.sources.PublicClient.get", side_effect=[f'<a href="{link}">Download</a>'.encode(), (FIXTURES / "nsdl.csv").read_bytes()]) as get:
            rows = _fetch_nsdl()
        self.assertEqual(rows[0]["report_as_of"], "2026-11-04")
        self.assertEqual(get.call_args_list[1].args[0], link)

    def test_public_client_refuses_offsite_discovered_links(self):
        client = PublicClient({"nsdl.com"})
        for url in ["http://nsdl.com/file.csv", "https://evil.example/file.csv", "https://user@nsdl.com/a", "https://nsdl.com:8443/a"]:
            with self.assertRaises(ValueError):
                client.validate(url)

    def test_xls_missing_optional_reader_is_actionable(self):
        with patch.dict("sys.modules", {"xlrd": None}):
            with self.assertRaisesRegex(ValueError, "save the report as .xlsx/CSV"):
                read_tables(b"\xd0\xcf\x11\xe0" + b"0" * 20, "report.xls")

    def test_xlsx_reads_typed_dates_and_percentages(self):
        buffer = BytesIO()
        with ZipFile(buffer, "w") as book:
            book.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><workbookPr/></workbook>')
            book.writestr("xl/styles.xml", '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><cellXfs><xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="10"/></cellXfs></styleSheet>')
            book.writestr("xl/worksheets/sheet1.xml", '''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
<row r="1"><c r="A1" t="inlineStr"><is><t>ISIN</t></is></c><c r="B1" t="inlineStr"><is><t>Coupon Rate</t></is></c><c r="C1" t="inlineStr"><is><t>Maturity Date</t></is></c></row>
<row r="2"><c r="A2" t="inlineStr"><is><t>INE03K307215</t></is></c><c r="B2" s="2"><v>0.108</v></c><c r="C2" s="1"><v>47424</v></c></row>
</sheetData></worksheet>''')
        row = parse_reference(buffer.getvalue(), "report.xlsx")[0]
        self.assertAlmostEqual(row["coupon_pct"], 10.8)
        self.assertIsNotNone(row["maturity_date"])
        self.assertNotEqual(row["maturity_date"], "47424")


class MultiSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "bonds.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def ingest(self, source, fixture, as_of="2026-11-06"):
        return import_report(self.db, source, FIXTURES / fixture, as_of)

    def test_failed_source_does_not_prevent_another_source_update(self):
        def fail():
            raise OSError("source unavailable")
        nse = replace(SOURCES["nse"], fetch=lambda: parse_nse((FIXTURES / "nse.csv").read_bytes()))
        grip = replace(SOURCES["grip"], fetch=fail)
        with patch.dict(SOURCES, nse=nse, grip=grip):
            result = refresh(self.db, source_ids=["nse", "grip"])
        self.assertEqual(result["status"], "partial")
        market = read_market(self.db)
        self.assertEqual(len([b for b in market["bonds"] if b["source_id"] == "nse"]), 2)
        self.assertEqual(next(s for s in market["sources"] if s["id"] == "grip")["status"], "error")
        self.assertEqual(market["source"]["mode"], "snapshot")

    def test_each_source_has_own_cooldown(self):
        self.ingest("grip", "grip.html")
        nse = replace(SOURCES["nse"], fetch=lambda: parse_nse((FIXTURES / "nse.csv").read_bytes()))
        with patch.dict(SOURCES, nse=nse):
            self.assertTrue(refresh(self.db, source_ids=["nse"])["ok"])
            self.assertEqual(refresh(self.db, source_ids=["nse"])["status"], "cooldown")

    def test_isin_enrichment_preserves_quotes_and_basis(self):
        self.ingest("grip", "grip.html")
        self.ingest("nsdl", "nsdl.csv")
        market = read_market(self.db)
        rows = [b for b in market["bonds"] if b["isin"] == "INE03K307215"]
        self.assertEqual({b["source_id"] for b in rows}, {"bondbazaar", "nsdl", "grip"})
        old = next(b for b in rows if b["source_id"] == "bondbazaar")
        self.assertEqual(old["yield_pct"], 11.95)
        self.assertEqual(old["yield_kind"], "quoted")
        self.assertEqual(old["maturity_date"], "2029-11-02")
        self.assertEqual(old["field_sources"]["maturity_date"]["source_id"], "nsdl")
        self.assertIsNone(old["quote_at"])
        self.assertEqual(len({b["id"] for b in rows}), 3)
        groups = {g["kind"]: g for g in market["overview"]["yield_groups"]}
        self.assertEqual(groups["indicative"]["yield_count"], 2)
        self.assertEqual(groups["quoted"]["max_yield"], 11.95)

    def test_conflicting_reference_does_not_overwrite_source_maturity(self):
        self.ingest("grip", "grip.html")
        report = Path(self.temp.name) / "different.csv"
        report.write_text("ISIN,Maturity Date\nINE03K307215,01-Jan-2030\n")
        import_report(self.db, "nsdl", report, "2026-11-06")
        row = next(b for b in read_market(self.db)["bonds"] if b["source_id"] == "grip" and b["isin"] == "INE03K307215")
        self.assertEqual(row["maturity_date"], "2029-11-02")
        self.assertEqual(row["conflicts"][0]["reference_value"], "2030-01-01")

    def test_default_status_applies_to_matching_isin_only_and_excludes_statistics(self):
        report = Path(self.temp.name) / "defaults.csv"
        report.write_text("ISIN\nINE03K307215\n")
        import_report(self.db, "nsdl-defaults", report, "2026-11-06")
        data = read_market(self.db)
        old = next(b for b in data["bonds"] if b["source_id"] == "bondbazaar" and b["isin"] == "INE03K307215")
        self.assertEqual(old["instrument_status"], "defaulted")
        self.assertEqual(data["overview"]["yield_count"], 22)
        other = next(b for b in data["bonds"] if b["isin"] == "INE477L07AN9")
        self.assertEqual(other["instrument_status"], "unknown")

    def test_import_failure_retains_successful_source_batch(self):
        self.ingest("grip", "grip.html")
        before = [b for b in read_market(self.db)["bonds"] if b["source_id"] == "grip"]
        report = Path(self.temp.name) / "bad.html"
        report.write_text("<html>Access denied</html>")
        with self.assertRaises(ValueError):
            import_report(self.db, "grip", report)
        after = [b for b in read_market(self.db)["bonds"] if b["source_id"] == "grip"]
        self.assertEqual(before, after)
        self.assertTrue(all(b["data_mode"] == "imported" for b in after))
        source = next(s for s in read_market(self.db)["sources"] if s["id"] == "grip")
        self.assertIsNone(source["attempted_at"], "Importing must not fabricate a network-fetch attempt")
        self.assertEqual(source["status"], "imported")

    def test_newer_reference_status_can_supersede_an_older_default(self):
        report = Path(self.temp.name) / "defaults.csv"
        report.write_text("ISIN\nINE03K307215\n")
        import_report(self.db, "nsdl-defaults", report, "2024-01-01")
        self.ingest("nsdl", "nsdl.csv", "2026-11-06")
        rows = [b for b in read_market(self.db)["bonds"] if b["isin"] == "INE03K307215"]
        self.assertTrue(all(row["instrument_status"] == "outstanding" for row in rows))

    def test_old_database_migration_preserves_batch_and_state(self):
        seed = json.loads((Path(__file__).parent.parent / "data" / "snapshot.json").read_text())
        with sqlite3.connect(self.db) as db:
            db.executescript("CREATE TABLE batches(id INTEGER PRIMARY KEY, observed_at TEXT, mode TEXT, payload TEXT); CREATE TABLE source_state(name TEXT PRIMARY KEY, attempted_at TEXT, status TEXT, error TEXT);")
            db.execute("INSERT INTO batches VALUES(1, ?, 'snapshot', ?)", (seed["observed_at"], json.dumps(seed["bonds"])))
            db.execute("INSERT INTO source_state VALUES('Bondbazaar', ?, 'error', 'old error')", (seed["observed_at"],))
        market = read_market(self.db)
        self.assertEqual(len(market["bonds"]), 28)
        self.assertEqual(market["source"]["error"], "old error")
        with connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM batches").fetchone()[0], 1)

    def test_older_report_cannot_replace_newer_source_batch(self):
        self.ingest("nsdl", "nsdl.csv", "2026-11-06")
        before = [b for b in read_market(self.db)["bonds"] if b["source_id"] == "nsdl"]
        with self.assertRaisesRegex(ValueError, "older than the stored"):
            self.ingest("nsdl", "nsdl.csv", "2024-01-01")
        self.assertEqual(before, [b for b in read_market(self.db)["bonds"] if b["source_id"] == "nsdl"])


if __name__ == "__main__":
    unittest.main()
