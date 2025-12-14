; FolderLens - Inno Setup Installer Script
; Requires Inno Setup 6.x: https://jrsoftware.org/isinfo.php

#define MyAppName "FolderLens"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "FolderLens"
#define MyAppURL "https://github.com/folderlens"
#define MyAppExeName "FolderLens.exe"
#define MyAppDescription "Folder Size Analyzer"

[Setup]
AppId={{A8F5E2C1-9D4B-4E6A-B3C7-1F2D8E9A0B5C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes

OutputDir=..\installer_output
OutputBaseFilename=FolderLens_Setup_{#MyAppVersion}
; SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes

WizardStyle=modern
WizardSizePercent=120
WizardResizable=no

MinVersion=10.0

PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog

ShowLanguageDialog=auto

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
english.ContextMenuIntegration=Add to Windows Explorer context menu
english.LaunchAfterInstall=Launch FolderLens after installation

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "contextmenu"; Description: "{cm:ContextMenuIntegration}"; GroupDescription: "Windows Integration:"; Flags: checkedonce

[Files]
Source: "..\dist\FolderLens.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "{#MyAppDescription}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; Comment: "{#MyAppDescription}"

[Registry]
Root: HKCR; Subkey: "Directory\Background\shell\FolderLens"; ValueType: string; ValueName: ""; ValueData: "Analyze with FolderLens"; Tasks: contextmenu; Flags: uninsdeletekey
Root: HKCR; Subkey: "Directory\Background\shell\FolderLens"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName}"; Tasks: contextmenu
Root: HKCR; Subkey: "Directory\Background\shell\FolderLens\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%V"""; Tasks: contextmenu

Root: HKCR; Subkey: "Directory\shell\FolderLens"; ValueType: string; ValueName: ""; ValueData: "Analyze with FolderLens"; Tasks: contextmenu; Flags: uninsdeletekey
Root: HKCR; Subkey: "Directory\shell\FolderLens"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#MyAppExeName}"; Tasks: contextmenu
Root: HKCR; Subkey: "Directory\shell\FolderLens\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: contextmenu

Root: HKLM; Subkey: "SOFTWARE\FolderLens"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "SOFTWARE\FolderLens"; ValueType: string; ValueName: "Version"; ValueData: "{#MyAppVersion}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchAfterInstall}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\cache"
