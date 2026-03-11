import importlib.util
import json
import os
import uuid
from pathlib import Path


def load_app(tmp_path):
    os.environ["GGHM_DATA_DIR"] = str(tmp_path)
    module_name = f"app_module_{uuid.uuid4().hex}"
    app_path = Path(__file__).resolve().parents[1] / "web" / "app.py"
    spec = importlib.util.spec_from_file_location(module_name, app_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_normalize_settings(tmp_path):
    app = load_app(tmp_path)
    out = app._normalize_settings({})
    assert out["invert"] is False
    assert len(out["deadband"]) == 5
    assert len(out["min_interval_ms"]) == 5
    assert out["com_port"] == ""
    assert out["safe_mode"] is True


def test_map_channel_updates_file(tmp_path):
    app = load_app(tmp_path)
    client = app.app.test_client()
    resp = client.post("/map_channel", json={"slider": 1, "channel": "media"})
    assert resp.status_code in (200, 302)
    with open(tmp_path / "sliders.json", "r") as f:
        data = json.load(f)
    assert data[0]["channel"] == "media"


def test_settings_update(tmp_path):
    app = load_app(tmp_path)
    client = app.app.test_client()
    resp = client.post("/settings/update", json={"slider": 2, "deadband": 5, "min_interval_ms": 100})
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["deadband"][1] == 5
    assert payload["min_interval_ms"][1] == 100


def test_values_endpoint_no_cache(tmp_path):
    app = load_app(tmp_path)
    (tmp_path / "values.json").write_text(json.dumps({"values": [1, 2, 3, 4, 5], "ts": 123}))
    client = app.app.test_client()
    resp = client.get("/values")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["values"] == [1, 2, 3, 4, 5]
    assert "no-store" in resp.headers.get("Cache-Control", "")


def test_serial_ports_endpoint(tmp_path):
    app = load_app(tmp_path)

    class Port:
        def __init__(self, device, description):
            self.device = device
            self.description = description

    app.serial.tools.list_ports.comports = lambda: [Port("COM3", "USB Serial Port")]
    client = app.app.test_client()
    resp = client.get("/serial/ports")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ports"][0]["device"] == "COM3"


def test_sonar_status_ok(tmp_path):
    app = load_app(tmp_path)

    class FakeSonar:
        def __init__(self):
            self.streamer_mode = True

    app.Sonar = FakeSonar
    client = app.app.test_client()
    resp = client.get("/sonar/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["streamer_mode"] is True
