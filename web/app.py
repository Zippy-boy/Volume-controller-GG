from flask import Flask, render_template, request, redirect, url_for, jsonify
import os
if os.environ.get("GGHM_TESTING") == "1":
    FlaskUI = None
else:
    try:
        from flaskwebgui import FlaskUI
    except Exception:
        FlaskUI = None
import traceback
import json
import os
from pathlib import Path
from steelseries_sonar_py import Sonar
import serial.tools.list_ports
import sys
import winreg
import time


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

BASE_DIR = ASSET_DIR
ROOT_DIR = ASSET_DIR.parent
app = Flask(
    __name__,
    template_folder=str(ASSET_DIR / "templates"),
    static_folder=str(ASSET_DIR / "static"),
)


sonar = None
last_sonar_try = 0.0
last_sonar_status = None
last_sonar_status_check = 0.0
CHANNELS = ["master", "game", "chatRender", "media", "aux"]
CHANNEL_LABELS = {
    "master": "master",
    "game": "game",
    "chatRender": "chat",
    "media": "media",
    "aux": "aux",
}

# Check if sliders.json exists, if not, create it
if not os.path.exists(DATA_DIR / 'sliders.json'):
    with open(DATA_DIR / 'sliders.json', 'w') as file:
        base = [
            {"slider": 1, "channel": "master"},
            {"slider": 2, "channel": "game"},
            {"slider": 3, "channel": "chatRender"},
            {"slider": 4, "channel": "media"},
            {"slider": 5, "channel": "aux"},
        ]
        json.dump(base, file)

# Check if settings.json exists, if not, create it
if not os.path.exists(DATA_DIR / 'settings.json'):
    with open(DATA_DIR / 'settings.json', 'w') as file:
        json.dump({
            "invert": False,
            "deadband": [2, 2, 2, 2, 2],
            "min_interval_ms": [30, 30, 30, 30, 30],
            "smoothing": [False, False, False, False, False],
            "com_port": "",
            "safe_mode": True,
            "startup_enabled": False
        }, file)

# Check if values.json exists, if not, create it
if not os.path.exists(DATA_DIR / 'values.json'):
    with open(DATA_DIR / 'values.json', 'w') as file:
        json.dump({"values": [0, 0, 0, 0, 0], "ts": 0}, file)

SETTINGS_FILE = DATA_DIR / "settings.json"
VALUES_FILE = DATA_DIR / "values.json"
SETTINGS_CHECK_INTERVAL = 0.5
VALUES_CHECK_INTERVAL = 0.3
_settings_cache = None
_settings_mtime = None
_settings_last_check = 0.0
_values_cache = {"values": [0, 0, 0, 0, 0], "ts": 0}
_values_mtime = None
_values_last_check = 0.0

def _normalize_settings(settings):
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
    return settings

def get_settings(force=False):
    global _settings_cache, _settings_mtime, _settings_last_check
    now = time.time()
    if not force and _settings_cache is not None and now - _settings_last_check < SETTINGS_CHECK_INTERVAL:
        return _settings_cache
    _settings_last_check = now
    try:
        mtime = SETTINGS_FILE.stat().st_mtime
    except FileNotFoundError:
        _settings_cache = _normalize_settings({})
        return _settings_cache
    if force or _settings_cache is None or _settings_mtime != mtime:
        with open(SETTINGS_FILE, "r") as file:
            _settings_cache = _normalize_settings(json.load(file))
        _settings_mtime = mtime
    return _settings_cache

def set_settings(settings):
    global _settings_cache, _settings_mtime, _settings_last_check
    normalized = _normalize_settings(settings)
    with open(SETTINGS_FILE, "w") as file:
        json.dump(normalized, file)
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
        with open(VALUES_FILE, "r") as file:
            _values_cache = json.load(file)
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
            "streamer_mode": bool(sonar.streamer_mode)
        }
    else:
        last_sonar_status = {
            "ok": False,
            "error": "Sonar not available"
        }
    return last_sonar_status

def change_volume(channel, percentage):
    if not ensure_sonar():
        return
    if sonar.streamer_mode:
        sonar.set_volume(channel, float(percentage), streamer_slider="monitoring")
    else:
        sonar.set_volume(channel, float(percentage))

@app.route('/')
def index_page():
    with open(DATA_DIR / "sliders.json", "r") as file:
        slider_map = json.load(file)
    settings = get_settings()
    return render_template(
        'index.html',
        channels=CHANNELS,
        channel_labels=CHANNEL_LABELS,
        slider_map=slider_map,
        settings=settings,
    )

@app.route('/map_channel', methods=['POST'])
def map_channel():
 # Lets the front end map slider -> Sonar channel
    input_data = request.get_json()
    slider_index = int(input_data['slider'])
    channel = input_data['channel']

    if channel not in CHANNELS:
        return redirect(url_for('index_page'))

    with open(DATA_DIR / "sliders.json", "r") as file:
        slider_map = json.load(file)

    for item in slider_map:
        if int(item["slider"]) == slider_index:
            item["channel"] = channel
            break

    with open(DATA_DIR / "sliders.json", "w") as file:
        json.dump(slider_map, file)
    return redirect(url_for('index_page'))

@app.route('/change_volume', methods=['POST'])
def change_volume_route():
    # This lists through all channels in the slider and changes their volume
    input_data = request.get_json()
    slider_index = input_data['slider']
    percentage = input_data['volume']
    settings = get_settings()
    if settings.get("invert"):
        percentage = 1 - float(percentage)
    with open(DATA_DIR / "sliders.json", "r") as file:
        slider_map = json.load(file)
        channel = slider_map[int(slider_index) - 1]["channel"]
        change_volume(channel, percentage)
        return redirect(url_for('index_page'))

@app.route('/values', methods=['GET'])
def values():
    resp = jsonify(get_values())
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp

@app.route('/toggle_invert', methods=['POST'])
def toggle_invert():
    settings = get_settings()
    settings["invert"] = not settings.get("invert", False)
    set_settings(settings)
    return settings

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

@app.route('/startup/status', methods=['GET'])
def startup_status():
    server_cmd = _get_run_value("GGHardwareServer")
    enabled = server_cmd is not None
    return jsonify({"enabled": enabled})

@app.route('/startup/enable', methods=['POST'])
def startup_enable():
    if getattr(sys, "frozen", False):
        server_exe = Path(sys.executable).with_name("GGHardwareServer.exe")
        _set_run_value("GGHardwareServer", f'\"{server_exe}\"')
        settings = get_settings()
        settings["startup_enabled"] = True
        set_settings(settings)
        return jsonify({"enabled": True})
    python_path = sys.executable
    pythonw_path = python_path.replace("python.exe", "pythonw.exe")
    if os.path.exists(pythonw_path):
        python_path = pythonw_path
    server_script = Path(__file__).resolve().parents[1] / "pc" / "server.py"
    _set_run_value("GGHardwareServer", f'\"{python_path}\" \"{server_script}\"')
    settings = get_settings()
    settings["startup_enabled"] = True
    set_settings(settings)
    return jsonify({"enabled": True})

@app.route('/startup/disable', methods=['POST'])
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

@app.route('/settings/update', methods=['POST'])
def settings_update():
    input_data = request.get_json()
    settings = get_settings()
    slider = int(input_data.get("slider", 0))
    if slider < 1 or slider > 5:
        return jsonify(settings)
    idx = slider - 1
    deadband = int(input_data.get("deadband", settings["deadband"][idx]))
    min_interval_ms = int(input_data.get("min_interval_ms", settings["min_interval_ms"][idx]))
    smoothing = input_data.get("smoothing", settings["smoothing"][idx])
    smoothing = bool(int(smoothing)) if isinstance(smoothing, (int, str)) else bool(smoothing)
    settings["deadband"][idx] = max(0, deadband)
    settings["min_interval_ms"][idx] = max(0, min_interval_ms)
    settings["smoothing"][idx] = smoothing
    set_settings(settings)
    return jsonify(settings)

@app.route('/settings/toggle_safe', methods=['POST'])
def settings_toggle_safe():
    settings = get_settings()
    settings["safe_mode"] = True
    set_settings(settings)
    return jsonify(settings)

@app.route('/serial/ports', methods=['GET'])
def serial_ports():
    ports = []
    for port in serial.tools.list_ports.comports():
        ports.append({"device": port.device, "description": port.description})
    return jsonify({"ports": ports})

@app.route('/serial/select', methods=['POST'])
def serial_select():
    input_data = request.get_json()
    settings = get_settings()
    settings["com_port"] = str(input_data.get("com_port", "") or "")
    set_settings(settings)
    return jsonify(settings)

@app.route('/sonar/status', methods=['GET'])
def sonar_status():
    return jsonify(get_sonar_status())

if __name__ == '__main__':
    # Runs the app in FlaskUI which just opens up a 
    # web browser and runs the app
    log_path = DATA_DIR / "web.log"
    if FlaskUI is not None:
        try:
            FlaskUI(app=app, server="flask").run()
            sys.exit(0)
        except Exception:
            log_path.write_text(traceback.format_exc())
    # No browser fallback. Show error and exit.
    sys.exit(1)
