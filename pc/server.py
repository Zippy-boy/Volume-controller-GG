from __future__ import print_function

import json
import os
import subprocess
import sys
import threading
import time
import ctypes
from pathlib import Path
from threading import Event

import pystray
import serial
import serial.tools.list_ports
from PIL import Image, ImageDraw
from steelseries_sonar_py import Sonar
import winreg

def _get_asset_dir():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "web"
    return Path(__file__).resolve().parents[1] / "web"

def _get_data_dir():
    if getattr(sys, "frozen", False):
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
        return base / "GG Hardware Mixer"
    return Path(__file__).resolve().parents[1] / "web"

ASSET_DIR = _get_asset_dir()
DATA_DIR = _get_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)

MAP_FILE = DATA_DIR / "sliders.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
VALUES_FILE = DATA_DIR / "values.json"
ICON_PATH = ASSET_DIR / "static" / "icon.png"

CHANNELS = ["master", "game", "chatRender", "media", "aux"]
CHANNEL_LABELS = {
    "master": "master",
    "game": "game",
    "chatRender": "chat",
    "media": "media",
    "aux": "aux",
}
sonar = None
last_sonar_try = 0.0
ser = None
last_serial_try = 0.0
last_serial_error_time = 0.0
last_sonar_error_time = 0.0
last_error_msg = ""
last_values = [0, 0, 0, 0, 0]
values_lock = threading.Lock()
last_device_seen = 0.0
DEVICE_TIMEOUT = 2.5
map_mtime = None
map_last_check = 0.0
slider_map = None
settings_mtime = None
settings_last_check = 0.0
settings_data = {"invert": False, "com_port": "", "safe_mode": True}
MAP_CHECK_INTERVAL = 0.5
SETTINGS_CHECK_INTERVAL = 0.5
VALUES_WRITE_INTERVAL = 0.5
ICON_UPDATE_INTERVAL = 2.0
STARTUP_CHECK_INTERVAL = 60.0
SMOOTHING_ALPHA = 0.25
last_startup_check = 0.0

def ensure_data_files():
    if not MAP_FILE.exists():
        MAP_FILE.write_text(json.dumps([
            {"slider": 1, "channel": "master"},
            {"slider": 2, "channel": "game"},
            {"slider": 3, "channel": "chatRender"},
            {"slider": 4, "channel": "media"},
            {"slider": 5, "channel": "aux"},
        ]))
    if not SETTINGS_FILE.exists():
        SETTINGS_FILE.write_text(json.dumps({
            "invert": False,
            "deadband": [2, 2, 2, 2, 2],
            "min_interval_ms": [30, 30, 30, 30, 30],
            "smoothing": [False, False, False, False, False],
            "com_port": "",
            "safe_mode": True,
            "startup_enabled": False
        }))
    if not VALUES_FILE.exists():
        VALUES_FILE.write_text(json.dumps({"values": [0, 0, 0, 0, 0], "ts": 0}))

MUTEX_NAME = "GGHardwareMixerServer"
mutex = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
if ctypes.windll.kernel32.GetLastError() == 183:
    sys.exit(0)


def ensure_sonar():
    global last_sonar_try, sonar, last_sonar_error_time, last_error_msg
    now = time.time()
    if sonar is not None:
        return True
    if now - last_sonar_try < 2.0:
        return False
    last_sonar_try = now
    try:
        sonar = Sonar()
        return True
    except Exception as e:
        sonar = None
        last_sonar_error_time = time.time()
        last_error_msg = str(e)
        return False


def change_volume(channel, percentage):
    percentage = asd(int(percentage), 0, 100, 0, 1)
    if ensure_sonar():
        if sonar.streamer_mode:
            sonar.set_volume(channel, float(percentage), streamer_slider="monitoring")
        else:
            sonar.set_volume(channel, float(percentage))
    elif get_safe_mode():
        return


def getAudionoPort():
    ports = list(serial.tools.list_ports.comports())
    for port in ports:
        if "USB Serial Port" in port.description:
            return port.device
    return None


def try_connect_serial():
    global ser, last_serial_try, last_serial_error_time, last_error_msg
    now = time.time()
    if ser is not None and ser.is_open:
        return True
    if now - last_serial_try < 2.0:
        return False
    last_serial_try = now
    preferred = get_com_port()
    if preferred:
        available = [p.device for p in serial.tools.list_ports.comports()]
        if preferred in available:
            port = preferred
        else:
            port = getAudionoPort()
    else:
        port = getAudionoPort()
    if not port:
        return False
    try:
        ser = serial.Serial(str(port), timeout=1)
        return True
    except Exception as e:
        ser = None
        last_serial_error_time = time.time()
        last_error_msg = str(e)
        return False


def load_slider_map(force=False):
    global map_mtime, slider_map, map_last_check
    now = time.time()
    if not force and now - map_last_check < MAP_CHECK_INTERVAL:
        return
    map_last_check = now
    try:
        mtime = MAP_FILE.stat().st_mtime
    except FileNotFoundError:
        return
    if force or map_mtime != mtime:
        with open(MAP_FILE, "r") as file:
            slider_map = json.load(file)
        map_mtime = mtime


def load_settings(force=False):
    global settings_mtime, settings_data, settings_last_check
    now = time.time()
    if not force and now - settings_last_check < SETTINGS_CHECK_INTERVAL:
        return
    settings_last_check = now
    try:
        mtime = SETTINGS_FILE.stat().st_mtime
    except FileNotFoundError:
        return
    if force or settings_mtime != mtime:
        with open(SETTINGS_FILE, "r") as file:
            settings_data = json.load(file)
        settings_data.setdefault("invert", False)
        settings_data.setdefault("com_port", "")
        settings_data["safe_mode"] = True
        settings_data.setdefault("deadband", [2, 2, 2, 2, 2])
        settings_data.setdefault("min_interval_ms", [30, 30, 30, 30, 30])
        settings_data.setdefault("smoothing", [False, False, False, False, False])
        settings_data.setdefault("startup_enabled", False)
        if len(settings_data.get("deadband", [])) < 5:
            settings_data["deadband"] += [2] * (5 - len(settings_data["deadband"]))
        if len(settings_data.get("min_interval_ms", [])) < 5:
            settings_data["min_interval_ms"] += [30] * (5 - len(settings_data["min_interval_ms"]))
        if len(settings_data.get("smoothing", [])) < 5:
            settings_data["smoothing"] += [False] * (5 - len(settings_data["smoothing"]))
        settings_mtime = mtime


def getChannel(slider):
    load_slider_map()
    if not slider_map:
        return CHANNELS[0]
    idx = int(slider - 1)
    if idx < 0 or idx >= len(slider_map):
        return CHANNELS[0]
    return slider_map[idx].get("channel", CHANNELS[0])


def maybe_invert(value):
    load_settings()
    if settings_data.get("invert"):
        return 100 - value
    return value


def get_deadband(slider_index):
    load_settings()
    try:
        return int(settings_data.get("deadband", [2, 2, 2, 2, 2])[slider_index])
    except Exception:
        return 2


def get_min_interval(slider_index):
    load_settings()
    try:
        ms = int(settings_data.get("min_interval_ms", [30, 30, 30, 30, 30])[slider_index])
        return max(0, ms) / 1000.0
    except Exception:
        return 0.03


def get_com_port():
    load_settings()
    return settings_data.get("com_port") or ""


def get_safe_mode():
    load_settings()
    return bool(settings_data.get("safe_mode", True))


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


def ensure_startup_registered():
    global last_startup_check
    now = time.time()
    if now - last_startup_check < STARTUP_CHECK_INTERVAL:
        return
    last_startup_check = now
    load_settings()
    if not settings_data.get("startup_enabled", False):
        return
    if getattr(sys, "frozen", False):
        server_exe = Path(sys.executable)
        desired = f"\"{server_exe}\""
    else:
        python_path = sys.executable
        pythonw_path = python_path.replace("python.exe", "pythonw.exe")
        if os.path.exists(pythonw_path):
            python_path = pythonw_path
        server_script = Path(__file__).resolve()
        desired = f"\"{python_path}\" \"{server_script}\""
    current = _get_run_value("GGHardwareServer")
    if current != desired:
        _set_run_value("GGHardwareServer", desired)


def open_app(icon, item):
    if getattr(sys, "frozen", False):
        web_exe = Path(sys.executable).with_name("GGHardwareWeb.exe")
        subprocess.Popen([str(web_exe)], cwd=str(web_exe.parent))
        return
    python_path = sys.executable
    pythonw_path = python_path.replace("python.exe", "pythonw.exe")
    if os.path.exists(pythonw_path):
        python_path = pythonw_path
    subprocess.Popen([python_path, str(Path(__file__).resolve().parents[1] / "web" / "app.py")], cwd=str(Path(__file__).resolve().parents[1] / "web"))


def quit_action(icon, item):
    event.set()
    icon.stop()
    sys.exit(0)


def asd(num, in_min, in_max, out_min, out_max):
    return (num - in_min) * (out_max - out_min) / (in_max - in_min) + out_min


def make_status_icon(base_img, status):
    img = base_img.copy()
    draw = ImageDraw.Draw(img)
    color = {
        "connected": (74, 222, 128),
        "disconnected": (248, 113, 113),
        "error": (250, 204, 21),
    }.get(status, (248, 113, 113))
    r = max(6, img.size[0] // 6)
    x = img.size[0] - r - 2
    y = img.size[1] - r - 2
    draw.ellipse((x - r, y - r, x + r, y + r), fill=color, outline=(20, 20, 20))
    return img


def get_status():
    now = time.time()
    if last_serial_error_time and now - last_serial_error_time < 5:
        return "error"
    if last_device_seen and (now - last_device_seen) < DEVICE_TIMEOUT:
        return "connected"
    return "disconnected"


def read_serial_data(event: Event):
    global ser, last_device_seen
    def read_values(timeout_sec=2.5):
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if ser is None:
                return None
            try:
                line = ser.readline().decode("utf-8", "ignore").strip()
            except Exception:
                return None
            if not line:
                continue
            parts = [p.strip() for p in line.split(",") if p.strip() != ""]
            if len(parts) < 5:
                continue
            try:
                return list(map(int, parts[:5]))
            except ValueError:
                continue
        return None

    last_sent = [0.0, 0.0, 0.0, 0.0, 0.0]
    prev = None
    smoothing_state = [None, None, None, None, None]
    last_write = 0.0
    last_written_vals = None
    while True:
        if event.is_set():
            break

        if not try_connect_serial():
            time.sleep(0.3)
            continue

        vals = read_values()
        if vals is None:
            try:
                if ser is not None:
                    ser.close()
            except Exception:
                pass
            ser = None
            time.sleep(0.1)
            continue

        nob1, nob2, nob3, nob4, nob5 = vals
        last_device_seen = time.time()
        if prev is None:
            prev = [nob1, nob2, nob3, nob4, nob5]
        now = time.time()

        load_settings()
        load_slider_map()
        deadband = settings_data.get("deadband", [2, 2, 2, 2, 2])
        min_ms = settings_data.get("min_interval_ms", [30, 30, 30, 30, 30])
        invert = settings_data.get("invert", False)
        smoothing = settings_data.get("smoothing", [False, False, False, False, False])
        channels = None
        if slider_map:
            channels = [item.get("channel", CHANNELS[0]) for item in slider_map]

        effective_vals = [0, 0, 0, 0, 0]
        for idx, nob in enumerate([nob1, nob2, nob3, nob4, nob5]):
            if idx < len(smoothing) and smoothing[idx]:
                if smoothing_state[idx] is None:
                    smoothing_state[idx] = float(nob)
                else:
                    smoothing_state[idx] = (1.0 - SMOOTHING_ALPHA) * smoothing_state[idx] + SMOOTHING_ALPHA * float(nob)
                effective = int(round(smoothing_state[idx]))
            else:
                smoothing_state[idx] = None
                effective = nob
            effective_vals[idx] = effective

        send_enabled = True
        if prev is None:
            prev = effective_vals[:]
            send_enabled = False

        if send_enabled:
            for idx, effective in enumerate(effective_vals):
                try:
                    min_delta = int(deadband[idx])
                except Exception:
                    min_delta = 2
                try:
                    min_interval = max(0, int(min_ms[idx])) / 1000.0
                except Exception:
                    min_interval = 0.03
                if abs(effective - prev[idx]) >= min_delta and (now - last_sent[idx]) >= min_interval:
                    if channels and idx < len(channels):
                        channel = channels[idx]
                    else:
                        channel = CHANNELS[0]
                    value = 100 - effective if invert else effective
                    change_volume(channel, value)
                    last_sent[idx] = now
                    prev[idx] = effective

        with values_lock:
            last_values[0] = nob1
            last_values[1] = nob2
            last_values[2] = nob3
            last_values[3] = nob4
            last_values[4] = nob5

        # Write values at most 5x/sec, and only if changed.
        vals_list = [nob1, nob2, nob3, nob4, nob5]
        if last_written_vals != vals_list and (now - last_write) >= VALUES_WRITE_INTERVAL:
            try:
                tmp_path = str(VALUES_FILE) + ".tmp"
                with open(tmp_path, "w") as f:
                    json.dump({"values": vals_list, "ts": time.time()}, f)
                os.replace(tmp_path, VALUES_FILE)
                last_write = now
                last_written_vals = vals_list
            except Exception:
                pass


def icon_status_loop(icon, base_img):
    last_status = None
    last_title = None
    while not event.is_set():
        ensure_startup_registered()
        status = get_status()
        if status != last_status:
            icon.icon = make_status_icon(base_img, status)
            last_status = status
        with values_lock:
            vals = list(last_values)
        port = get_com_port() or "auto"
        load_slider_map()
        if slider_map:
            channels = [item.get("channel", CHANNELS[0]) for item in slider_map]
        else:
            channels = CHANNELS
        pairs = []
        for idx in range(min(5, len(vals))):
            label = CHANNEL_LABELS.get(channels[idx], channels[idx])
            pairs.append(f"{label}:{vals[idx]}")
        title = f"GG Mixer | {status} | {port} | " + " ".join(pairs)
        if title != last_title:
            icon.title = title
            last_title = title
        time.sleep(ICON_UPDATE_INTERVAL)


ensure_data_files()
event = Event()
base_image = Image.open(ICON_PATH)
icon = pystray.Icon(name="GG Hardware Mixer", icon=base_image, title="GG Hardware Mixer")
menu = (
    pystray.MenuItem("Open Web UI", open_app),
    pystray.MenuItem("Quit", quit_action),
)
icon.menu = pystray.Menu(*menu)
icon.on_click = open_app

threading.Thread(target=read_serial_data, args=(event,), daemon=True).start()
threading.Thread(target=icon_status_loop, args=(icon, base_image), daemon=True).start()

icon.run()
