#define AppName "OMR Exam App"
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#define AppPublisher "OMR Exam App"
#define AppExeName "OMRExamApp.exe"

[Setup]
AppId={{8B734070-1C2C-4F23-AC8D-C5BDE74A0C85}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\OMR Exam App
DefaultGroupName={#AppName}
OutputDir=..\dist\installer
OutputBaseFilename=OMRExamApp-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
ArchitecturesAllowed=x64
PrivilegesRequired=lowest
Uninstallable=yes
CloseApplications=yes
RestartApplications=no
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\OMRExamApp\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: postinstall nowait skipifsilent

; User data lives under %APPDATA%\OMR Exam App and is intentionally preserved by uninstall/upgrade.
