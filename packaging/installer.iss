; Inno Setup script for the Kovaak Agent desktop app.
; Build with packaging/build.ps1, which runs PyInstaller first and passes /DAppVersion.
; Installs per user (no admin prompt). User data in %APPDATA%\Kovaak Agent is kept on uninstall.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Kovaak Agent"
#define AppExe "KovaakAgent.exe"

[Setup]
AppId={{6B0F3E0A-5A1D-4C55-9C64-2C8D7B7E4A21}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Kovaak Agent contributors
AppPublisherURL=https://github.com/CalebLouwskitter/KovaakstoAgent
AppSupportURL=https://github.com/CalebLouwskitter/KovaakstoAgent/issues
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
AppMutex=KovaakAgentDesktop
CloseApplications=yes
SetupIconFile=..\web\favicon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir=..\dist
OutputBaseFilename=KovaakAgent-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked
Name: "startup"; Description: "&Start with Windows (opens in the tray, capturing new runs)"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "..\dist\KovaakAgent\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\samples\demo-session.csv"; DestDir: "{app}\samples"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "KovaakAgent"; ValueData: """{app}\{#AppExe}"" --minimized"; Tasks: startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "Open {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
const
  WebView2Key = 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function WebView2Installed(): Boolean;
var
  Version: String;
begin
  Result :=
    (RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKLM, WebView2Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKCU, WebView2Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and not WizardSilent() and not WebView2Installed() then
    MsgBox('Kovaak Agent needs the Microsoft Edge WebView2 Runtime, which was not found.' + #13#10 +
      'Install it from https://developer.microsoft.com/microsoft-edge/webview2/ if the window stays blank.',
      mbInformation, MB_OK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // The app can turn on start-with-Windows itself, so always remove the entry.
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'KovaakAgent');
end;
