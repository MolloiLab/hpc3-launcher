; Inno Setup script for the HPC3 Launcher Windows installer.
;
; Packages the PyInstaller folder build (dist\HPC3-Launcher) into a single
; download-and-run setup.exe -- the Windows analog of the macOS .dmg. Installs
; to Program Files, adds Start Menu (and optional desktop) shortcuts, and an
; uninstaller.
;
; Built in CI (see .github/workflows/release.yml):
;     iscc /DAppVersion=<version> windows-installer.iss
; -> HPC3-Launcher-<version>-windows-setup.exe in this directory.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppName=HPC3 Launcher
AppVersion={#AppVersion}
AppPublisher=Molloi Lab
DefaultDirName={autopf}\HPC3 Launcher
DefaultGroupName=HPC3 Launcher
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\HPC3-Launcher.exe
SetupIconFile=hpc3_launcher\resources\icon.ico
OutputDir=.
OutputBaseFilename=HPC3-Launcher-{#AppVersion}-windows-setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
; The whole PyInstaller one-folder build (HPC3-Launcher.exe + _internal\).
Source: "dist\HPC3-Launcher\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\HPC3 Launcher"; Filename: "{app}\HPC3-Launcher.exe"
Name: "{group}\Uninstall HPC3 Launcher"; Filename: "{uninstallexe}"
Name: "{autodesktop}\HPC3 Launcher"; Filename: "{app}\HPC3-Launcher.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\HPC3-Launcher.exe"; Description: "Launch HPC3 Launcher"; Flags: nowait postinstall skipifsilent
