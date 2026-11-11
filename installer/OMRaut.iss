#define AppName "OMRaut"
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#define AppPublisher "OMRaut"
#define AppExeName "OMRaut.exe"

[Setup]
AppId={{8B734070-1C2C-4F23-AC8D-C5BDE74A0C85}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\OMRaut
DefaultGroupName={#AppName}
OutputDir=..\dist\installer
OutputBaseFilename=OMRaut-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
ArchitecturesAllowed=x64
PrivilegesRequired=lowest
Uninstallable=yes
CloseApplications=yes
RestartApplications=no
WizardStyle=modern
SetupIconFile=OMRaut.ico
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\OMRaut\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: postinstall nowait skipifsilent

; User data lives under %APPDATA%\OMRaut (or the earlier %APPDATA%\OMR Exam App folder, if present) and is intentionally preserved by uninstall/upgrade.
