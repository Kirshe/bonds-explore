"""Static build, resume and Pages persistence checks without external networking."""

from dataclasses import replace
from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urljoin

from bondscope.publishing import STATIC_FILES, base_path, build_site, preview_handler, restore_state, save_state
from bondscope.sources import SOURCES
from bondscope.store import ROOT, import_report, read_market

FIXTURES = Path(__file__).parent / "fixtures"


class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "bonds.sqlite3"
        self.output = self.root / "dist"
        self.state = self.root / "state.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_offline_build_contains_only_public_static_assets(self):
        with patch("bondscope.publishing.refresh") as fetch:
            result = build_site(self.db, self.output, state_output=self.state)
        fetch.assert_not_called()
        self.assertEqual(result["observations"], 28)
        self.assertEqual({p.name for p in self.output.iterdir()}, STATIC_FILES)
        html = (self.output / "index.html").read_text()
        self.assertIn('data-mode="static"', html)
        for path in ("./app.js", "./styles.css", "./favicon.svg"):
            self.assertIn(path, html)
            self.assertTrue(urljoin("https://example.github.io/bondscrapper/", path).startswith("https://example.github.io/bondscrapper/"))
        self.assertNotIn('href="/"', html)
        self.assertNotIn('src="/', html)
        market = json.loads((self.output / "market.json").read_text())
        self.assertEqual(market["publication"]["mode"], "static")
        self.assertEqual(market["source"]["mode"], "snapshot")
        self.assertIsNone(market["bonds"][0]["quote_at"])
        self.assertEqual(len(market["sources"]), 8)

    def test_state_roundtrip_preserves_each_source_and_raw_provenance(self):
        import_report(self.db, "nse", FIXTURES / "nse.csv", "2026-11-06")
        import_report(self.db, "nsdl", FIXTURES / "nsdl.csv", "2026-11-06")
        before = read_market(self.db)
        saved = save_state(self.db, self.state)
        fresh_db = self.root / "restored.sqlite3"
        restore_state(fresh_db, self.state)
        after = read_market(fresh_db)
        self.assertEqual(before["bonds"], after["bonds"])
        self.assertEqual(before["sources"], after["sources"])
        self.assertEqual(len(saved["batches"]), 3)
        # State contains raw observations, not the enriched public response.
        raw_bondbazaar = next(b for b in saved["batches"] if b["source_id"] == "bondbazaar")
        self.assertIsNone(raw_bondbazaar["records"][15]["maturity_date"])
        self.assertNotIn("attempt_id", json.dumps(saved))

    def test_failed_collection_publishes_retained_rows_and_persists_error(self):
        import_report(self.db, "grip", FIXTURES / "grip.html")
        before = read_market(self.db)
        def fail():
            raise OSError("source offline")
        with patch.dict(SOURCES, grip=replace(SOURCES["grip"], fetch=fail)):
            result = build_site(self.db, self.output, scrape=True, source_ids=["grip"], state_output=self.state)
        self.assertEqual(result["collection"]["status"], "error")
        market = json.loads((self.output / "market.json").read_text())
        source = next(s for s in market["sources"] if s["id"] == "grip")
        previous = next(s for s in before["sources"] if s["id"] == "grip")
        self.assertEqual(source["observed_at"], previous["observed_at"])
        self.assertEqual(source["count"], 2)
        self.assertIn("source offline", source["error"])
        restored = self.root / "resume.sqlite3"
        restore_state(restored, self.state)
        self.assertEqual(next(s for s in read_market(restored)["sources"] if s["id"] == "grip")["status"], "error")

    def test_restore_refuses_to_replace_existing_local_data(self):
        save_state(self.db, self.state)
        before = read_market(self.db)
        with self.assertRaisesRegex(ValueError, "empty database"):
            restore_state(self.db, self.state)
        self.assertEqual(before, read_market(self.db))

    def test_corrupt_state_is_not_treated_as_a_new_empty_collection(self):
        self.state.write_text('{"schema_version":99}')
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            build_site(self.db, self.output, state_input=self.state)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.db.exists())

    def test_incomplete_state_does_not_damage_a_previous_build(self):
        build_site(self.db, self.output, state_output=self.state)
        previous = (self.output / "market.json").read_bytes()
        data = json.loads(self.state.read_text())
        data["batches"][0]["records"][0]["source"] = None
        data["batches"][0]["source_id"] = "unknown-provider"
        self.state.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            build_site(self.root / "fresh.sqlite3", self.output, state_input=self.state)
        self.assertEqual(previous, (self.output / "market.json").read_bytes())

    def test_build_refuses_unrelated_files_and_private_state_in_site(self):
        self.output.mkdir()
        important = self.output / "notes.txt"
        important.write_text("Keep this file")
        with self.assertRaisesRegex(ValueError, "unrelated files"):
            build_site(self.db, self.output)
        self.assertEqual(important.read_text(), "Keep this file")
        with self.assertRaisesRegex(ValueError, "outside the published"):
            build_site(self.db, self.root / "other-output", state_output=self.root / "other-output" / "state.json")
        with self.assertRaisesRegex(ValueError, "separate"):
            build_site(self.db, ROOT / "web")

    def test_interrupted_fetch_restores_as_error_with_last_good_batch(self):
        data = save_state(self.db, self.state)
        data["source_states"] = [{"name": "bondbazaar", "attempted_at": "2026-11-07T00:00:00+00:00", "status": "fetching", "error": None}]
        self.state.write_text(json.dumps(data))
        fresh = self.root / "fresh.sqlite3"
        restore_state(fresh, self.state)
        market = read_market(fresh)
        self.assertEqual(len(market["bonds"]), 28)
        self.assertEqual(market["source"]["status"], "error")
        self.assertIn("interrupted", market["source"]["error"])

    def test_cli_build_and_resume_need_no_github_or_network(self):
        command = [sys.executable, "-m", "bondscope", "--db", str(self.db), "build", "--output", str(self.output), "--state-output", str(self.state)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Built 28 observations", result.stdout)
        resume = [sys.executable, "-m", "bondscope", "--db", str(self.root / "restored.sqlite3"), "build", "--output", str(self.output), "--restore-state", str(self.state)]
        result = subprocess.run(resume, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads((self.output / "market.json").read_text())["bonds"]), 28)

    def test_strict_build_reports_collection_failure_without_losing_site(self):
        from bondscope.__main__ import main
        def fail():
            raise OSError("offline")
        args = ["bondscope", "--db", str(self.db), "build", "--scrape", "--source", "grip", "--strict", "--output", str(self.output)]
        with patch.object(sys, "argv", args), patch.dict(SOURCES, grip=replace(SOURCES["grip"], fetch=fail)), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(main(), 1)
        self.assertTrue((self.output / "market.json").is_file())

    def request(self, mount, path, method="GET"):
        handler = preview_handler(self.output, mount)
        request = f"{method} {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        output = BytesIO()
        connection = SimpleNamespace(makefile=lambda *args: BytesIO(request.encode()), sendall=output.write)
        server = SimpleNamespace(server_name="localhost", server_port=8000)
        with patch("bondscope.publishing.StaticHandler.log_message"):
            handler(connection, ("127.0.0.1", 12345), server)
        header, _, body = output.getvalue().partition(b"\r\n\r\n")
        return int(header.split()[1]), header, body

    def test_static_preview_serves_root_and_project_paths_without_api(self):
        build_site(self.db, self.output)
        for mount in ("/", "/bondscrapper/"):
            for name in ("", "index.html", "app.js", "styles.css", "market.json", "favicon.svg"):
                status, headers, body = self.request(mount, mount + name)
                self.assertEqual(status, 200, (mount, name))
                self.assertIn(b"Cache-Control: no-store", headers)
                self.assertTrue(body)
            self.assertEqual(self.request(mount, mount + "api/bonds")[0], 404)
            self.assertEqual(self.request(mount, mount + "data/bonds.sqlite3")[0], 404)
            self.assertEqual(self.request(mount, mount + "../README.md")[0], 404)
            self.assertEqual(self.request(mount, mount + "index.html", "HEAD")[2], b"")
        self.assertEqual(self.request("/bondscrapper/", "/app.js")[0], 404)
        self.assertEqual(self.request("/bondscrapper/", "/bondscrapper")[0], 301)

    def test_preview_base_path_validation(self):
        self.assertEqual(base_path("/bondscrapper"), "/bondscrapper/")
        for value in ("https://example.com/", "../repo/", "/../repo/", "/repo//", "/repo?x=1", "/%2e%2e/"):
            with self.assertRaises(ValueError):
                base_path(value)


class PagesStateBranchTests(unittest.TestCase):
    """Exercise the workflow's actual Git persistence using a local bare remote."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.remote = self.root / "origin.git"
        self.repo = self.root / "repo"
        self.run_git("init", "--bare", str(self.remote))
        self.run_git("init", "-b", "master", str(self.repo))
        (self.repo / "README.md").write_text("Original application code")
        self.run_git("-C", str(self.repo), "add", "README.md")
        self.run_git("-C", str(self.repo), "-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-m", "Initial code")
        self.run_git("-C", str(self.repo), "remote", "add", "origin", str(self.remote))
        self.run_git("-C", str(self.repo), "push", "-u", "origin", "master")

    def tearDown(self):
        self.temp.cleanup()

    def run_git(self, *args):
        return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout

    def script(self, operation, worktree, state, *, check=True):
        return subprocess.run(["bash", str(ROOT / "scripts" / "pages-state.sh"), operation, str(worktree), str(state)], cwd=self.repo, capture_output=True, text=True, check=check)

    def test_bootstrap_and_next_run_retain_last_good_data_on_own_branch(self):
        prior = self.root / "prior.json"
        worktree = self.root / "first state tree"
        self.script("restore", worktree, prior)
        self.assertFalse(prior.exists())
        state = self.root / "new.json"
        db = self.root / "first.sqlite3"
        import_report(db, "grip", FIXTURES / "grip.html")
        initial = save_state(db, state)
        self.script("save", worktree, state)
        self.assertEqual(self.run_git("--git-dir", str(self.remote), "ls-tree", "--name-only", "bondscope-data").strip(), "state.json")
        second = self.root / "second-tree"
        self.script("restore", second, prior)
        self.assertEqual(json.loads(prior.read_text()), initial)
        fresh = self.root / "second.sqlite3"
        restore_state(fresh, prior)
        with patch("bondscope.publishing.refresh", return_value={"ok": False, "status": "error", "message": "offline", "results": []}):
            build_site(fresh, self.root / "dist", scrape=True, state_output=state)
        self.script("save", second, state)
        persisted = json.loads(self.run_git("--git-dir", str(self.remote), "show", "bondscope-data:state.json"))
        self.assertEqual(persisted["batches"], initial["batches"])
        self.assertEqual((self.repo / "README.md").read_text(), "Original application code")
        self.assertEqual(self.run_git("-C", str(self.repo), "branch", "--show-current").strip(), "master")

    def test_remote_lookup_failure_does_not_bootstrap_empty_state(self):
        self.run_git("-C", str(self.repo), "remote", "set-url", "origin", str(self.root / "missing.git"))
        worktree = self.root / "state-tree"
        result = self.script("restore", worktree, self.root / "state.json", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(worktree.exists())


if __name__ == "__main__":
    unittest.main()
