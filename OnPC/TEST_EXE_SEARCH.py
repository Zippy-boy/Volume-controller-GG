import os

def search_exe(paths):
    exe = []
    for path in paths:
        for root, dirs, files in os.walk(path):
            for file in files:
                if file.endswith('.exe'):
                    exe.append(file)
    
    return exe

print(search_exe(("D:\SteamLibrary\steamapps\common", "C:\Program Files (x86)\Steam\steamapps\common")))