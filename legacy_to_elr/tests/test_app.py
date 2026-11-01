"""
Checks the web interface: pages load, forms are protected, the convert and
mapping loop works, and URLs can't reach files outside the app's own folders.

Run from the project folder:  python -m unittest discover tests -v
"""
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as appmod  # noqa: E402
import config  # noqa: E402


class TestApp(unittest.TestCase):
    def setUp(self):
        # Work on copies, so tests never change the real mapping files
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (config.MAPPINGS_DIR, config.LEGACY_DIR)
        shutil.copytree(config.MAPPINGS_DIR, self.tmp / "mappings")
        config.MAPPINGS_DIR = self.tmp / "mappings"
        config.LEGACY_DIR = self.tmp / "legacy"
        self.client = appmod.create_app(runs_dir=self.tmp / "runs").test_client()

    def tearDown(self):
        config.MAPPINGS_DIR, config.LEGACY_DIR = self.saved
        shutil.rmtree(self.tmp)

    def token(self, page="/"):
        html = self.client.get(page).text
        return re.search(r'name="csrf_token" value="([0-9a-f]+)"', html).group(1)

    def convert_sample(self, source_id):
        r = self.client.post("/convert", data={"csrf_token": self.token(), "source_id": source_id,
                                               "input_choice": "sample"})
        self.assertEqual(r.status_code, 302)
        return r.headers["Location"]

    def sent_count(self, html):
        return int(re.search(r'<dt>Sent</dt>\s*<dd class="count">(\d+)</dd>', html).group(1))

    def test_home_page_shows_the_synthetic_data_warning(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("synthetic data only", r.text)

    def test_forms_without_a_token_are_refused(self):
        r = self.client.post("/convert", data={"source_id": "RVC", "input_choice": "sample"})
        self.assertEqual(r.status_code, 400)

    def test_converting_a_sample_shows_outcomes_and_follow_up(self):
        html = self.client.get(self.convert_sample("RVC")).text
        self.assertEqual(self.sent_count(html), 42)
        self.assertIn("Mumps IgM", html)
        self.assertIn("Unreadable collection date", html)

    def test_wrong_source_gets_a_clear_message(self):
        self.convert_sample("CHL")   # creates the sample files
        chl_file = config.LEGACY_DIR / "chl_lab_export.csv"
        with open(chl_file, "rb") as f:
            r = self.client.post("/convert", data={"csrf_token": self.token(), "source_id": "RRL",
                                                   "input_choice": "upload", "export_file": (f, "chl.csv")},
                                 content_type="multipart/form-data", follow_redirects=True)
        self.assertIn("does not look like a Regional Reference Laboratory export", r.text)

    def test_mapping_then_converting_again_sends_more_messages(self):
        run_url = self.convert_sample("RVC")
        run_id = run_url.rsplit("/", 1)[-1]
        before = self.sent_count(self.client.get(run_url).text)
        self.client.post("/mappings/add", data={"csrf_token": self.token(), "run_id": run_id,
                                                "kind": "result value", "source_id": "RVC",
                                                "local_value": "Pos.", "standard_code": "10828004"})
        r = self.client.post(f"/runs/{run_id}/rerun", data={"csrf_token": self.token()})
        after = self.sent_count(self.client.get(r.headers["Location"]).text)
        self.assertEqual(after, before + 2)

    def test_test_code_mapping_needs_confirmation_and_a_verified_code(self):
        base = {"run_id": "", "kind": "test code", "source_id": "RVC", "local_value": "Mumps IgM"}
        r = self.client.post("/mappings/add", data={**base, "csrf_token": self.token(),
                                                    "standard_code": "13950-1"}, follow_redirects=True)
        self.assertIn("Confirm that the lab", r.text)
        r = self.client.post("/mappings/add", data={**base, "csrf_token": self.token(), "confirmed": "yes",
                                                    "standard_code": "99999-9"}, follow_redirects=True)
        self.assertIn("verified list", r.text)

    def test_message_page_explains_each_field(self):
        run_url = self.convert_sample("CHL")
        html = self.client.get(run_url).text
        message_url = re.search(r'href="(/runs/[^"]+/messages/[^"]+)"', html).group(1)
        r = self.client.get(message_url)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Patient name (last^first)", r.text)

    def test_urls_cannot_reach_other_files(self):
        run_url = self.convert_sample("CHL")
        self.assertEqual(self.client.get("/runs/../../config.py").status_code, 404)
        self.assertEqual(self.client.get(f"{run_url}/download/meta.json").status_code, 404)
        self.assertEqual(self.client.get(f"{run_url}/download/..%2Fmeta.json").status_code, 404)
        self.assertEqual(self.client.get(f"{run_url}/download/elr_messages.hl7").status_code, 200)


if __name__ == "__main__":
    unittest.main()
