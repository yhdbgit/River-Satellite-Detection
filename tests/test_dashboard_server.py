import importlib.util
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "run_dashboard.py"
SPEC = importlib.util.spec_from_file_location("run_dashboard", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class DashboardServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_selection_path = MODULE.SELECTION_PATH
        MODULE.SELECTION_PATH = (
            Path(self.temporary_directory.name) / "selected_sites.json"
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), MODULE.DashboardHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        MODULE.SELECTION_PATH = self.original_selection_path
        self.temporary_directory.cleanup()

    def request_json(self, path, payload=None):
        data = None
        headers = {}
        method = "GET"
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
            method = "POST"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_empty_selection(self):
        status, payload = self.request_json("/api/selections")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"selected_ids": [], "sites": []})

    def test_save_exactly_five_sites(self):
        selected_ids = [f"site-{index}" for index in range(5)]
        sites = [{"id": site_id} for site_id in selected_ids]
        status, payload = self.request_json(
            "/api/selections",
            {"selected_ids": selected_ids, "sites": sites},
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["saved"])
        saved = json.loads(MODULE.SELECTION_PATH.read_text(encoding="utf-8"))
        self.assertEqual(saved["selected_ids"], selected_ids)
        self.assertEqual(len(saved["sites"]), 5)

    def test_rejects_less_than_five_sites(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.request_json(
                "/api/selections",
                {
                    "selected_ids": ["site-1"],
                    "sites": [{"id": "site-1"}],
                },
            )
        self.assertEqual(context.exception.code, 400)
        self.assertFalse(MODULE.SELECTION_PATH.exists())


if __name__ == "__main__":
    unittest.main()
