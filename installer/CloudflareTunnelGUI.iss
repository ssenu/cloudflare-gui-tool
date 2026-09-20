; Cloudflare Tunnel GUI — 윈도우 설치 프로그램 (Inno Setup 6)
;
; 무엇을 만드나: dist\CloudflareTunnelGUI\ (PyInstaller onedir 결과물)을
; 통째로 담은 설치 exe 하나. 설치하면 프로그램 폴더에 풀리고, 시작 메뉴와
; (선택 시) 바탕화면에 바로가기가 생기며, 제거 프로그램이 함께 깔린다.
;
; 왜 폴더가 아니라 설치본으로 주나: onedir은 파일이 183개라 그대로 건네면
; "어느 걸 실행하죠?"가 된다. 설치본은 그 폴더를 한 곳에 풀고 바로가기만
; 보여 준다. 실행 속도는 onefile의 1.6초 -> 0.8초로 줄어든다(실측).
;
; 관리자 권한을 요구하지 않는다(PrivilegesRequired=lowest): 사용자 폴더에
; 설치하므로 UAC 창이 뜨지 않고, 회사·학교 PC에서도 설치할 수 있다.
;
; 빌드:
;   pyinstaller --noconfirm CFT-onedir.spec
;   ISCC.exe installer\CloudflareTunnelGUI.iss
; 결과: installer\out\CloudflareTunnelGUI-Setup-<버전>.exe

#define AppName "Cloudflare Tunnel GUI"
#define AppExeName "Cloudflare Tunnel GUI.exe"
#define AppPublisher "ssenu"
#define AppURL "https://github.com/ssenu/cloudflare-gui-tool"

; 버전은 빌드할 때 넘겨줄 수 있다: ISCC /DAppVersion=1.2.3 ...
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
; AppId는 절대 바꾸지 않는다. 이 값으로 "같은 프로그램의 업그레이드"인지
; 판단하므로, 바꾸면 기존 설치를 지우지 못하고 두 벌이 남는다.
AppId={{8F3C5A21-9E4D-4B77-A1E6-5D2C7B9F0A34}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=out
OutputBaseFilename=CloudflareTunnelGUI-Setup-{#AppVersion}
SetupIconFile=..\assets\cloudflare_logo.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
; 압축률: LZMA2/max 가 exe 크기를 가장 많이 줄인다(설치 시간은 조금 늘어난다).
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; onedir 결과물 전체. recursesubdirs로 _internal 안까지 같이 담는다.
Source: "..\dist\CloudflareTunnelGUI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 앱이 실행 중에 만드는 것들. 설정 파일(%APPDATA%\CloudflareTunnelGUI)은
; 일부러 남긴다 - 재설치하면 터널·서버 등록이 그대로 살아 있어야 한다.
Type: filesandordirs; Name: "{app}\_internal\__pycache__"
Type: dirifempty; Name: "{app}"
