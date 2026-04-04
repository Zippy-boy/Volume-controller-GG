import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "web" / "app.py"


def load_app_module(data_dir):
    os.environ["GGHM_TESTING"] = "1"
    os.environ["GGHM_DATA_DIR"] = str(data_dir)
    os.environ["GGHM_ASSET_DIR"] = str(ROOT / "web")
    spec = importlib.util.spec_from_file_location("gghm_test_web_app", APP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.app_module = load_app_module(self.data_dir)
        self.client = self.app_module.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()
        os.environ.pop("GGHM_TESTING", None)
        os.environ.pop("GGHM_DATA_DIR", None)
        os.environ.pop("GGHM_ASSET_DIR", None)

    def read_json(self, name):
        return json.loads((self.data_dir / name).read_text(encoding="utf-8"))

    def test_safe_mode_toggle_persists(self):
        before = self.read_json("settings.json")
        self.assertTrue(before["safe_mode"])

        response = self.client.post("/settings/toggle_safe")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["safe_mode"])

        after = self.read_json("settings.json")
        self.assertFalse(after["safe_mode"])

    def test_change_volume_respects_invert_setting(self):
        settings = self.read_json("settings.json")
        settings["invert"] = True
        self.app_module.set_settings(settings)

        calls = []

        def fake_change_volume(channel, percentage):
            calls.append((channel, percentage))

        self.app_module.change_volume = fake_change_volume

        response = self.client.post("/change_volume", json={"slider": 1, "volume": 0.2})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["channel"], "master")
        self.assertAlmostEqual(payload["volume"], 0.8)
        self.assertEqual(calls, [("master", 0.8)])

    def test_map_channel_rejects_invalid_channel(self):
        response = self.client.post("/map_channel", json={"slider": 1, "channel": "invalid"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.get_json())

    def test_startup_status_repairs_stale_setting(self):
        settings = self.read_json("settings.json")
        settings["startup_enabled"] = True
        self.app_module.set_settings(settings)
        self.app_module._get_run_value = lambda name: None

        response = self.client.get("/startup/status")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["enabled"])
        repaired = self.read_json("settings.json")
        self.assertFalse(repaired["startup_enabled"])

    def test_values_endpoint_normalizes_short_payload(self):
        (self.data_dir / "values.json").write_text(json.dumps({"values": [12, 200], "ts": "3"}), encoding="utf-8")
        self.app_module._values_last_check = 0
        self.app_module._values_mtime = None

        response = self.client.get("/values")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"values": [12, 100, 0, 0, 0], "ts": 3.0})


if __name__ == "__main__":
    unittest.main()
