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
sonar = None
last_sonar_try = 0.0
ser = None
last_serial_try = 0.0
last_error_time = 0.0
last_error_msg = ""
last_values = [0, 0, 0, 0, 0]
values_lock = threading.Lock()
map_mtime = None
slider_map = None
settings_mtime = None
settings_data = {"invert": False, "com_port": "", "safe_mode": True}

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
            "com_port": "",
            "safe_mode": True
        }))
    if not VALUES_FILE.exists():
        VALUES_FILE.write_text(json.dumps({"values": [0, 0, 0, 0, 0], "ts": 0}))

MUTEX_NAME = "GGHardwareMixerServer"
mutex = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
if ctypes.windll.kernel32.GetLastError() == 183:
    sys.exit(0)


def ensure_sonar():
    global last_sonar_try, sonar, last_error_time, last_error_msg
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
        last_error_time = time.time()
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
    global ser, last_serial_try, last_error_time, last_error_msg
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
        last_error_time = time.time()
        last_error_msg = str(e)
        return False


def load_slider_map(force=False):
    global map_mtime, slider_map
    try:
        mtime = MAP_FILE.stat().st_mtime
    except FileNotFoundError:
        return
    if force or map_mtime != mtime:
        with open(MAP_FILE, "r") as file:
            slider_map = json.load(file)
        map_mtime = mtime


def load_settings(force=False):
    global settings_mtime, settings_data
    try:
        mtime = SETTINGS_FILE.stat().st_mtime
    except FileNotFoundError:
        return
    if force or settings_mtime != mtime:
        with open(SETTINGS_FILE, "r") as file:
            settings_data = json.load(file)
        settings_data.setdefault("invert", False)
        settings_data.setdefault("com_port", "")
        settings_data.setdefault("safe_mode", True)
        settings_data.setdefault("deadband", [2, 2, 2, 2, 2])
        settings_data.setdefault("min_interval_ms", [30, 30, 30, 30, 30])
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
    if last_error_time and now - last_error_time < 5:
        return "error"
    if ser is not None and ser.is_open:
        return "connected"
    return "disconnected"


def read_serial_data(event: Event):
    global ser
    def read_values():
        while True:
            if ser is None:
                return None
            try:
                line = ser.readline().decode("utf-8", "ignore").strip()
            except Exception:
                return None
            parts = [p.strip() for p in line.split(",") if p.strip() != ""]
            if len(parts) < 5:
                continue
            try:
                return list(map(int, parts[:5]))
            except ValueError:
                continue

    last_sent = [0.0, 0.0, 0.0, 0.0, 0.0]
    prev = None
    while True:
        if event.is_set():
            break

        if not try_connect_serial():
            time.sleep(0.3)
            continue

        vals = read_values()
        if vals is None:
            ser = None
            time.sleep(0.1)
            continue

        nob1, nob2, nob3, nob4, nob5 = vals
        if prev is None:
            prev = [nob1, nob2, nob3, nob4, nob5]
        now = time.time()

        for idx, nob in enumerate([nob1, nob2, nob3, nob4, nob5]):
            min_delta = get_deadband(idx)
            min_interval = get_min_interval(idx)
            if abs(nob - prev[idx]) >= min_delta and (now - last_sent[idx]) >= min_interval:
                channel = getChannel(idx + 1)
                change_volume(channel, maybe_invert(nob))
                last_sent[idx] = now
                prev[idx] = nob

        with values_lock:
            last_values[0] = nob1
            last_values[1] = nob2
            last_values[2] = nob3
            last_values[3] = nob4
            last_values[4] = nob5

        try:
            tmp_path = str(VALUES_FILE) + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump({"values": [nob1, nob2, nob3, nob4, nob5], "ts": time.time()}, f)
            os.replace(tmp_path, VALUES_FILE)
        except Exception:
            pass


def icon_status_loop(icon, base_img):
    while not event.is_set():
        status = get_status()
        icon.icon = make_status_icon(base_img, status)
        with values_lock:
            vals = list(last_values)
        port = get_com_port() or "auto"
        icon.title = f"GG Mixer - {status} - {port} - {vals}"
        time.sleep(0.5)


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
