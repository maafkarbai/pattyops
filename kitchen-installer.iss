; Build after kitchen-app.spec with Inno Setup 6.
[Setup]
AppId=PattyOpsKitchen
AppName=PattyOps Kitchen
AppVersion=0.1.0
DefaultDirName={localappdata}\Programs\PattyOps Kitchen
DefaultGroupName=PattyOps Kitchen
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=PattyOps-Kitchen-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=assets\pattyops.ico
UninstallDisplayIcon={app}\PattyOps Kitchen.exe
CloseApplications=yes

[Files]
Source: "dist\PattyOps Kitchen\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\PattyOps Kitchen"; Filename: "{app}\PattyOps Kitchen.exe"
Name: "{autodesktop}\PattyOps Kitchen"; Filename: "{app}\PattyOps Kitchen.exe"

[Run]
Filename: "{app}\PattyOps Kitchen.exe"; Description: "Open PattyOps Kitchen"; Flags: nowait postinstall skipifsilent
