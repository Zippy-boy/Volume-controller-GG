from flask import Flask, render_template, request, jsonify
import json
import os
from pathlib import Path
import sys
import time
import traceback
import winreg

import serial.tools.list_ports
from steelseries_sonar_py import Sonar

if os.environ.get("GGHM_TESTING") == "1":
    FlaskUI = None
else:
    try:
        from flaskwebgui import FlaskUI
    except Exception:
        FlaskUI = None


def _get_asset_dir():
    env_asset = os.environ.get("GGHM_ASSET_DIR")
    if env_asset:
        return Path(env_asset)
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "web"
    return Path(__file__).resolve().parent


def _get_data_dir():
    env_data = os.environ.get("GGHM_DATA_DIR")
    if env_data:
        return Path(env_data)
    if getattr(sys, "frozen", False):
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
        return base / "GG Hardware Mixer"
    return Path(__file__).resolve().parent


ASSET_DIR = _get_asset_dir()
DATA_DIR = _get_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(
    __name__,
    template_folder=str(ASSET_DIR / "templates"),
    static_folder=str(ASSET_DIR / "static"),
)

CHANNELS = ["master", "game", "chatRender", "media", "aux"]
CHANNEL_LABELS = {
    "master": "master",
    "game": "game",
    "chatRender": "chat",
    "media": "media",
    "aux": "aux",
}
DEFAULT_SLIDER_MAP = [
    {"slider": 1, "channel": "master"},
    {"slider": 2, "channel": "game"},
    {"slider": 3, "channel": "chatRender"},
    {"slider": 4, "channel": "media"},
    {"slider": 5, "channel": "aux"},
]
DEFAULT_SETTINGS = {
    "invert": False,
    "deadband": [2, 2, 2, 2, 2],
    "min_interval_ms": [30, 30, 30, 30, 30],
    "smoothing": [False, False, False, False, False],
    "com_port": "",
    "safe_mode": True,
    "startup_enabled": False,
}
DEFAULT_VALUES = {"values": [0, 0, 0, 0, 0], "ts": 0}

MAP_FILE = DATA_DIR / "sliders.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
VALUES_FILE = DATA_DIR / "values.json"
SETTINGS_CHECK_INTERVAL = 0.5
VALUES_CHECK_INTERVAL = 0.3

sonar = None
last_sonar_try = 0.0
last_sonar_status = None
last_sonar_status_check = 0.0
_settings_cache = None
_settings_mtime = None
_settings_last_check = 0.0
_values_cache = DEFAULT_VALUES.copy()
_values_mtime = None
_values_last_check = 0.0


def _write_json(path, data):
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as file:
        json.dump(data, file)
    os.replace(tmp_path, path)


def ensure_data_files():
    if not MAP_FILE.exists():
        _write_json(MAP_FILE, DEFAULT_SLIDER_MAP)
    if not SETTINGS_FILE.exists():
        _write_json(SETTINGS_FILE, DEFAULT_SETTINGS)
    if not VALUES_FILE.exists():
        _write_json(VALUES_FILE, DEFAULT_VALUES)


def _normalize_settings(settings):
    settings = dict(settings or {})
    settings.setdefault("invert", False)
    settings.setdefault("deadband", [2, 2, 2, 2, 2])
    settings.setdefault("min_interval_ms", [30, 30, 30, 30, 30])
    settings.setdefault("smoothing", [False, False, False, False, False])
    settings.setdefault("com_port", "")
    settings.setdefault("safe_mode", True)
    settings.setdefault("startup_enabled", False)
    if len(settings["deadband"]) < 5:
        settings["deadband"] += [2] * (5 - len(settings["deadband"]))
    if len(settings["min_interval_ms"]) < 5:
        settings["min_interval_ms"] += [30] * (5 - len(settings["min_interval_ms"]))
    if len(settings["smoothing"]) < 5:
        settings["smoothing"] += [False] * (5 - len(settings["smoothing"]))
    settings["deadband"] = [max(0, int(v)) for v in settings["deadband"][:5]]
    settings["min_interval_ms"] = [max(0, int(v)) for v in settings["min_interval_ms"][:5]]
    settings["smoothing"] = [bool(v) for v in settings["smoothing"][:5]]
    settings["invert"] = bool(settings["invert"])
    settings["safe_mode"] = bool(settings["safe_mode"])
    settings["startup_enabled"] = bool(settings["startup_enabled"])
    settings["com_port"] = str(settings["com_port"] or "")
    return settings


def _normalize_slider_map(slider_map):
    normalized = []
    items = slider_map if isinstance(slider_map, list) else []
    for index in range(5):
        item = items[index] if index < len(items) and isinstance(items[index], dict) else {}
        channel = item.get("channel", CHANNELS[index])
        if channel not in CHANNELS:
            channel = CHANNELS[index]
        normalized.append({"slider": index + 1, "channel": channel})
    return normalized


def _normalize_values(payload):
    payload = payload if isinstance(payload, dict) else {}
    values = payload.get("values", DEFAULT_VALUES["values"])
    if not isinstance(values, list):
        values = DEFAULT_VALUES["values"]
    normalized_values = []
    for index in range(5):
        try:
            normalized_values.append(max(0, min(100, int(values[index]))))
        except (IndexError, TypeError, ValueError):
            normalized_values.append(0)
    try:
        timestamp = float(payload.get("ts", 0))
    except (TypeError, ValueError):
        timestamp = 0
    return {"values": normalized_values, "ts": timestamp}


def get_slider_map():
    try:
        with open(MAP_FILE, "r", encoding="utf-8") as file:
            slider_map = _normalize_slider_map(json.load(file))
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        slider_map = _normalize_slider_map(DEFAULT_SLIDER_MAP)
        _write_json(MAP_FILE, slider_map)
    return slider_map


def set_slider_map(slider_map):
    _write_json(MAP_FILE, _normalize_slider_map(slider_map))


def get_settings(force=False):
    global _settings_cache, _settings_mtime, _settings_last_check
    now = time.time()
    if not force and _settings_cache is not None and now - _settings_last_check < SETTINGS_CHECK_INTERVAL:
        return _settings_cache
    _settings_last_check = now
    try:
        mtime = SETTINGS_FILE.stat().st_mtime
    except FileNotFoundError:
        _settings_cache = _normalize_settings(DEFAULT_SETTINGS)
        return _settings_cache
    if force or _settings_cache is None or _settings_mtime != mtime:
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as file:
                _settings_cache = _normalize_settings(json.load(file))
        except (json.JSONDecodeError, TypeError):
            _settings_cache = _normalize_settings(DEFAULT_SETTINGS)
            _write_json(SETTINGS_FILE, _settings_cache)
        _settings_mtime = mtime
    return _settings_cache


def set_settings(settings):
    global _settings_cache, _settings_mtime, _settings_last_check
    normalized = _normalize_settings(settings)
    _write_json(SETTINGS_FILE, normalized)
    try:
        _settings_mtime = SETTINGS_FILE.stat().st_mtime
    except FileNotFoundError:
        _settings_mtime = None
    _settings_cache = normalized
    _settings_last_check = time.time()


def ensure_sonar():
    global sonar, last_sonar_try
    now = time.time()
    if sonar is not None:
        return True
    if now - last_sonar_try < 2.0:
        return False
    last_sonar_try = now
    try:
        sonar = Sonar()
        return True
    except Exception:
        sonar = None
        return False


def get_values():
    global _values_cache, _values_mtime, _values_last_check
    now = time.time()
    if now - _values_last_check < VALUES_CHECK_INTERVAL:
        return _values_cache
    _values_last_check = now
    try:
        mtime = VALUES_FILE.stat().st_mtime
    except FileNotFoundError:
        return _values_cache
    if _values_mtime != mtime:
        try:
            with open(VALUES_FILE, "r", encoding="utf-8") as file:
                payload = json.load(file)
        except (json.JSONDecodeError, TypeError):
            payload = DEFAULT_VALUES
        _values_cache = _normalize_values(payload)
        _values_mtime = mtime
    return _values_cache


def get_sonar_status():
    global last_sonar_status, last_sonar_status_check
    now = time.time()
    if last_sonar_status is not None and now - last_sonar_status_check < 2.0:
        return last_sonar_status
    last_sonar_status_check = now
    if ensure_sonar():
        last_sonar_status = {
            "ok": True,
            "streamer_mode": bool(sonar.streamer_mode),
        }
    else:
        last_sonar_status = {
            "ok": False,
            "error": "Sonar not available",
        }
    return last_sonar_status


def change_volume(channel, percentage):
    if not ensure_sonar():
        return
    percentage = max(0.0, min(1.0, float(percentage)))
    if sonar.streamer_mode:
        sonar.set_volume(channel, percentage, streamer_slider="monitoring")
    else:
        sonar.set_volume(channel, percentage)


@app.route("/")
def index_page():
    return render_template(
        "index.html",
        channels=CHANNELS,
        channel_labels=CHANNEL_LABELS,
        slider_map=get_slider_map(),
        settings=get_settings(),
    )


@app.route("/map_channel", methods=["POST"])
def map_channel():
    input_data = request.get_json(silent=True) or {}
    try:
        slider_index = int(input_data["slider"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Invalid slider"}), 400

    channel = input_data.get("channel")
    if channel not in CHANNELS:
        return jsonify({"error": "Invalid channel"}), 400

    slider_map = get_slider_map()
    if slider_index < 1 or slider_index > len(slider_map):
        return jsonify({"error": "Slider not found"}), 404

    slider_map[slider_index - 1]["channel"] = channel
    set_slider_map(slider_map)
    return jsonify({"ok": True, "slider_map": slider_map})


@app.route("/change_volume", methods=["POST"])
def change_volume_route():
    input_data = request.get_json(silent=True) or {}
    try:
        slider_index = int(input_data["slider"])
        percentage = float(input_data["volume"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Invalid payload"}), 400

    if slider_index < 1 or slider_index > 5:
        return jsonify({"error": "Invalid slider"}), 400

    percentage = max(0.0, min(1.0, percentage))
    settings = get_settings()
    if settings.get("invert"):
        percentage = 1.0 - percentage

    slider_map = get_slider_map()
    channel = slider_map[slider_index - 1]["channel"]
    change_volume(channel, percentage)
    return jsonify({"ok": True, "channel": channel, "volume": percentage})


@app.route("/values", methods=["GET"])
def values():
    resp = jsonify(get_values())
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


@app.route("/toggle_invert", methods=["POST"])
def toggle_invert():
    settings = get_settings()
    settings["invert"] = not settings.get("invert", False)
    set_settings(settings)
    return jsonify(settings)


def _startup_key():
    return winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0,
        winreg.KEY_READ | winreg.KEY_WRITE,
    )


def _get_run_value(name):
    try:
        with _startup_key() as key:
            return winreg.QueryValueEx(key, name)[0]
    except FileNotFoundError:
        return None


def _set_run_value(name, value):
    with _startup_key() as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


@app.route("/startup/status", methods=["GET"])
def startup_status():
    settings = get_settings()
    enabled = bool(settings.get("startup_enabled", False))
    server_cmd = _get_run_value("GGHardwareServer")

    if enabled and not server_cmd:
        settings["startup_enabled"] = False
        set_settings(settings)
        enabled = False
    elif not enabled and server_cmd:
        settings["startup_enabled"] = True
        set_settings(settings)
        enabled = True

    return jsonify({"enabled": enabled})


@app.route("/startup/enable", methods=["POST"])
def startup_enable():
    if getattr(sys, "frozen", False):
        server_exe = Path(sys.executable).with_name("GGHardwareServer.exe")
        _set_run_value("GGHardwareServer", f'"{server_exe}"')
    else:
        python_path = sys.executable
        pythonw_path = python_path.replace("python.exe", "pythonw.exe")
        if os.path.exists(pythonw_path):
            python_path = pythonw_path
        server_script = Path(__file__).resolve().parents[1] / "pc" / "server.py"
        _set_run_value("GGHardwareServer", f'"{python_path}" "{server_script}"')

    settings = get_settings()
    settings["startup_enabled"] = True
    set_settings(settings)
    return jsonify({"enabled": True})


@app.route("/startup/disable", methods=["POST"])
def startup_disable():
    try:
        with _startup_key() as key:
            try:
                winreg.DeleteValue(key, "GGHardwareServer")
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        pass

    settings = get_settings()
    settings["startup_enabled"] = False
    set_settings(settings)
    return jsonify({"enabled": False})


@app.route("/settings/update", methods=["POST"])
def settings_update():
    input_data = request.get_json(silent=True) or {}
    settings = get_settings()
    try:
        slider = int(input_data.get("slider", 0))
    except (TypeError, ValueError):
        slider = 0
    if slider < 1 or slider > 5:
        return jsonify({"error": "Invalid slider"}), 400

    idx = slider - 1
    try:
        deadband = int(input_data.get("deadband", settings["deadband"][idx]))
    except (TypeError, ValueError):
        deadband = settings["deadband"][idx]
    try:
        min_interval_ms = int(input_data.get("min_interval_ms", settings["min_interval_ms"][idx]))
    except (TypeError, ValueError):
        min_interval_ms = settings["min_interval_ms"][idx]

    smoothing = input_data.get("smoothing", settings["smoothing"][idx])
    smoothing = bool(int(smoothing)) if isinstance(smoothing, (int, str)) else bool(smoothing)

    settings["deadband"][idx] = max(0, deadband)
    settings["min_interval_ms"][idx] = max(0, min_interval_ms)
    settings["smoothing"][idx] = smoothing
    set_settings(settings)
    return jsonify(settings)


@app.route("/settings/toggle_safe", methods=["POST"])
def settings_toggle_safe():
    settings = get_settings()
    settings["safe_mode"] = not settings.get("safe_mode", True)
    set_settings(settings)
    return jsonify(settings)


@app.route("/serial/ports", methods=["GET"])
def serial_ports():
    ports = []
    for port in serial.tools.list_ports.comports():
        ports.append({"device": port.device, "description": port.description})
    return jsonify({"ports": ports})


@app.route("/serial/select", methods=["POST"])
def serial_select():
    input_data = request.get_json(silent=True) or {}
    settings = get_settings()
    settings["com_port"] = str(input_data.get("com_port", "") or "")
    set_settings(settings)
    return jsonify(settings)


@app.route("/sonar/status", methods=["GET"])
def sonar_status():
    return jsonify(get_sonar_status())


ensure_data_files()


if __name__ == "__main__":
    log_path = DATA_DIR / "web.log"
    if FlaskUI is not None:
        try:
            FlaskUI(app=app, server="flask").run()
            sys.exit(0)
        except Exception:
            log_path.write_text(traceback.format_exc(), encoding="utf-8")
    app.run(host="127.0.0.1", port=5000, debug=False)
