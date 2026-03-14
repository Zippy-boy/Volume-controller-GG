# GG Hardware Mixer

Simple hardware knobs + SteelSeries GG Sonar mixer. The PC server reads the Arduino sliders and maps them to GG Sonar channels. The UI lets you pick the mapping and tune jitter/debounce.

## What it includes
- `arduino/main.cpp` � Arduino sketch (sends 5 slider values over serial)
- `pc/server.py` � tray server (reads serial, drives Sonar, shows status)
- `web/app.py` + `web/templates/index.html` � UI (mapping + tuning)

## Install from releases
https://github.com/Zippy-boy/Volume-controller-GG/releases

## Run locally
1. Install Python 3.11
2. `pip install -r requirements.txt`
3. `python pc/server.py`
4. `python web/app.py`

## Build installer (Windows)
1. `powershell -ExecutionPolicy Bypass -Feile .\build.ps1`
2. Open `installer.iss` in Inno Setup and build

## Notes
- GG Sonar must be installed and running.
- The UI is opened from the tray icon or by launching the app with `--ui`.
