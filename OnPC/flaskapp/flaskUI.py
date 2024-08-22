import pythoncom
from flask import Flask, render_template, request, redirect, url_for
from flaskwebgui import FlaskUI
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume, ISimpleAudioVolume
from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL
import json
import numpy as np
import os


app = Flask(__name__)


devices = AudioUtilities.GetSpeakers()
interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
master_volume = cast(interface, POINTER(IAudioEndpointVolume))

# Check if sliders.json exists, if not, create it
if not os.path.exists('sliders.json'):
    with open('sliders.json', 'w') as file:
        base = '[{"slider": 1, "apps": []}, {"slider": 2, "apps": []}, {"slider": 3, "apps": []}, {"slider": 4, "apps": []}]'
        json.dump(base, file)

# Check if minimum_value.txt exists, if not, create it
if not os.path.exists('minimum_value.txt'):
    with open('minimum_value.txt', 'w') as file:
        file.write('0')


def change_volume(app, percentage):
    # print(app, percentage)
    sessions = AudioUtilities.GetAllSessions()
    for session in sessions:
        volume = session._ctl.QueryInterface(ISimpleAudioVolume)
        if session.Process and session.Process.name() == app:
            volume.SetMasterVolume(percentage, None)

def find_open_apps(): # finds all open apps that are using audio
    pythoncom.CoInitialize()
    apps = []
    sessions = AudioUtilities.GetAllSessions()
    for session in sessions:
        if session.Process and session.Process.name() not in apps:
            apps.append(session.Process.name())
    return apps

def asd(num, in_min, in_max, out_min, out_max):
    return (num - in_min) * (out_max - out_min) / (in_max - in_min) + out_min

@app.route('/')
def index_page():
    pythoncom.CoInitialize()
    apps = find_open_apps()

    with open("appsInPath.txt", "r") as file:
        appsInPath = file.read().split("\n")
        appsInPath = [app for app in appsInPath if app != ""]
        print(appsInPath)

    with open("sliders.json", "r") as file:
        preLoadedApps = json.load(file)

        # print(preLoadedApps)
    return render_template('index.html', apps=apps, preLoadedApps=preLoadedApps, appsInPath=appsInPath)  # Loads the index.html file

@app.route('/submit', methods=['POST']) 
def submit():
 # Lets the front end send what apps are linked to what slider, called at every drag and drop   
    input_data = request.get_json()
    newSliderDict = []
    # print(input_data[0])
    for slider in input_data:
        apps = slider['apps']
        slider = slider['slider']
        newApps = []
        # print(slider, apps)
        for app in apps:
            newApps.append(app.replace("\n", "").replace(" ", "").strip())
        # print(newApps)
        newSliderDict.append({
            "slider": slider,
            "apps": newApps
        })

    with open("sliders.json", "w") as file:
        # Saves the apps and their sliders to sliders.json
        file.write(str(json.dumps(newSliderDict)).replace("\"", '"'))
    return redirect(url_for('index_page'))

@app.route('/change_volume', methods=['POST'])
def change_volume_route():
    # This lists thorugh all apps in the slider and changes their volume
    input_data = request.get_json()
    slider_index = input_data['slider']
    percentage = input_data['volume']
    with open("sliders.json", "r") as file:
        file = json.load(file)
        for app in file[int(slider_index)-1]["apps"]:
            # print(app, percentage)
            change_volume(app, percentage)
        return redirect(url_for('index_page'))

@app.route('/change_master_volume', methods=['POST'])
def change_master_volume():
    # This changes the master volume of the PC
    with open("minimum_value.txt", "r") as f:
        lowest_volume_limit = float(f.read())

    volume_level = request.get_json()['volume']
    volume_level = asd(int(volume_level), 0, 100, lowest_volume_limit, 0)

    print(f"soejf h0ipw: {(np.emath.logn(1.07346, volume_level)) - 65.5582}")
    mappedValue = (np.emath.logn(1.07346, volume_level)) - 65.5582
    mappedValue = np.clip(mappedValue, lowest_volume_limit, 0)

    master_volume.SetMasterVolumeLevel(int(mappedValue), None)
    return redirect(url_for('index_page'))

@app.route('/calibrate', methods=['POST'])
def calibrate():
    # This slowly lowers the volume to find where the code errors
    # which demonstrates the lowest volume the PC can go
    volume_level = 0
    while True:
        try:
            hr = master_volume.SetMasterVolumeLevel(volume_level, None)
            if hr == 0:
                volume_level -= 0.01
            else:
                break
        except Exception as e:
            break
    
    with open("minimum_value.txt", "w") as f:
        f.write(str(volume_level))

    return redirect(url_for('index_page'))


@app.route('/path_select', methods=['POST'])
def path_select():
    path = request.get_json()['path']
    print(path)

    # find all exe files in the directory
    apps = []
    for root, dirs, files in os.walk(path):
        for file in files:
            if file.endswith('.exe'):
                apps.append(file)

    with open("appsInPath.txt", "w") as f:
        for app in apps:
            f.write(app + "\n")

    return redirect(url_for('index_page'))




if __name__ == '__main__':
    # Runs the app in FlaskUI which just opens up a 
    # web browser and runs the app
    app.run(host="0.0.0.0", port=8000, debug=True)
    # FlaskUI(app=app, server="flask", height=1000, width=1000).run()
