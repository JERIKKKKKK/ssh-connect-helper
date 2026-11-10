; Inno Setup 6.3+ script. Keep this file ASCII-only: all Russian texts come from Russian.isl.
; Build with build_installer.bat (it passes /DAppVersion=...).
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "SSH Connect"
#define AppExe  "SSH_Connect.exe"

[Setup]
; Fixed AppId: a new installer updates the installed copy instead of installing a second one.
AppId={{B7C3E0A4-5D1F-4C8A-9E62-3F7A1D9C2B58}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=IT Admin
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=SSH_Connect_Setup_{#AppVersion}
SetupIconFile=..\assets\ssh_connect.ico
UninstallDisplayIcon={app}\ssh_connect.ico
UninstallDisplayName={#AppName}
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\ssh_connect.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\ssh_connect.ico"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; IconFilename: "{app}\ssh_connect.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
