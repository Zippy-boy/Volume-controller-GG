; Inno Setup Script
#define AppName "GG Hardware Mixer"
#define AppVersion "1.0.0"
#define AppPublisher "Zippy"
#define AppExeServer "GGHardwareServer.exe"
#define AppExeWeb "GGHardwareWeb.exe"

[Setup]
AppId={{A7C0C9F1-8E8B-4B42-9A19-8C8E6C1E9B6F}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=GGHardwareMixer-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
SetupIconFile=web\static\icon.ico

[Files]
Source: "dist\{#AppExeServer}"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\{#AppExeWeb}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeServer}"; Parameters: "--ui"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeServer}"; Parameters: "--ui"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop icon"; GroupDescription: "Additional icons:"; Flags: unchecked
Name: "startup"; Description: "Start GG Hardware Mixer on Windows startup"; GroupDescription: "Startup:"; Flags: checkedonce

[Run]
Filename: "{app}\{#AppExeServer}"; Description: "Start GG Hardware Mixer server"; Flags: nowait postinstall skipifsilent

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "GGHardwareServer"; ValueData: "{app}\{#AppExeServer}"; Tasks: startup

[UninstallDelete]
Type: files; Name: "{app}\{#AppExeServer}"
Type: files; Name: "{app}\{#AppExeWeb}"
Type: filesandordirs; Name: "{app}\web"
