# Cloudflare Tunnel GUI — 설계 스펙

날짜: 2026-08-10
상태: 승인됨

## 목적

Cloudflare Tunnel(cloudflared) 조작을 터미널 명령어 대신 GUI에서 빈칸 채우기 방식으로 수행하는 Windows 데스크톱 앱. 터널 목록 조회, 생성(create + config.yml + route dns), on/off, 웹서버 동시 실행, SSH를 통한 원격(Raspberry Pi) cloudflared 제어까지 지원한다.

## 전제 조건

- 도메인은 이미 Cloudflare에 연결되어 있음 (네임서버 설정 완료)
- 대상 OS: Windows 11 (로컬), Raspberry Pi OS (원격)
- cloudflared는 로컬에 이미 설치됨 (2026.7.3 확인). 온보딩에서 미설치 케이스도 처리
- Python 3.11+, PyQt6

## 확정된 요구사항

| 항목 | 결정 |
|------|------|
| 터널 on/off | GUI가 `cloudflared tunnel run`을 자식 프로세스로 관리 (서비스 등록 방식 아님) |
| 웹서버 실행 | 터널마다 자유 명령(uvicorn, npm 등) + 작업 폴더 등록, 단축키 지원 |
| SSH 원격 제어 | 원격 모드 전환 — 상단에서 로컬↔Rpi 전환 시 같은 화면으로 원격 제어 (paramiko) |
| 실시간 로그 | 터널/웹서버 stdout 실시간 스트림 뷰어 포함 |
| 미포함 (문서화만) | Quick Tunnel, Cloudflare Access, DNS 레코드 API 관리 → `docs/future-extensions.md` |

## 아키텍처

```
cloudflare-gui-tool/
├── app/
│   ├── main.py               # 진입점
│   ├── core/
│   │   ├── runner.py         # CommandRunner 추상화: LocalRunner / SshRunner(paramiko)
│   │   ├── cloudflared.py    # cloudflared CLI 래퍼 (list/create/delete/route/run)
│   │   ├── config_yml.py     # config.yml 생성·파싱
│   │   ├── store.py          # 앱 설정 JSON (서버 명령, SSH 프로필, 단축키)
│   │   └── process_mgr.py    # 터널/웹서버 프로세스 수명주기 + 로그 스트림
│   └── ui/
│       ├── main_window.py    # 메인: 터널 카드 리스트
│       ├── wizard.py         # 터널 생성 마법사
│       ├── log_viewer.py     # 실시간 로그 창
│       ├── onboarding.py     # 첫 실행: 설치 확인 + 로그인
│       ├── settings.py       # SSH 프로필, 단축키 설정
│       └── theme.py          # 다크 테마 QSS
├── tests/                    # core 단위 테스트 (pytest)
└── docs/
    └── future-extensions.md  # 확장 방안 문서
```

### 핵심 설계: CommandRunner 추상화

모든 cloudflared 및 서버 명령은 `CommandRunner` 인터페이스를 통해 실행한다.

- `LocalRunner`: QProcess 기반 로컬 실행
- `SshRunner`: paramiko SSH 채널 기반 원격 실행

인터페이스: `run(cmd) -> (exit_code, stdout, stderr)` (단발성), `spawn(cmd) -> ManagedProcess` (장기 실행 + 라인 스트림 콜백 + stop()).

상단 드롭다운으로 대상 머신을 전환하면 활성 Runner만 바뀌고 UI·기능은 동일하게 동작한다. 파일 조작(config.yml 읽기/쓰기)도 Runner를 경유한다 (로컬: 파일시스템, 원격: SFTP).

### 데이터 저장

- 앱 설정: `%APPDATA%/CloudflareTunnelGUI/settings.json`
  - 터널별 메타데이터(서버 명령, 작업 폴더, 함께 시작 여부), SSH 프로필 목록, 단축키
- cloudflared 설정: 표준 위치 (`~/.cloudflared/`) — config는 터널별 `~/.cloudflared/config-<터널이름>.yml`로 분리해 `tunnel run --config` 로 지정 (여러 터널 동시 실행 가능)
- SSH 비밀번호는 저장하지 않음. 키 파일 인증 권장, 비밀번호는 세션 중 메모리만

## 화면 사양

### 온보딩 (최초 1회)

1. cloudflared 설치 확인 (`cloudflared --version`) — 미설치 시 winget 설치 버튼 제공
2. `~/.cloudflared/cert.pem` 존재 확인 — 없으면 "Cloudflare 로그인" 버튼 → `cloudflared tunnel login` 실행 (브라우저 열림) → cert.pem 생성 폴링로 완료 감지
3. 완료 시 메인 화면으로. 이후 실행에서는 검사만 통과하면 바로 메인

### 메인 화면

- 상단 바: 대상 머신 드롭다운(이 PC / SSH 프로필들), 새로고침, `+` 터널 생성, 설정 버튼
- 터널 카드 목록 (`cloudflared tunnel list` 결과 + settings.json 메타 병합):
  - 터널 이름, 연결 도메인, 로컬 서비스 주소
  - 상태 표시등: ⚪ 중지 / 🟡 시작 중 / 🟢 실행 중 / 🔴 오류
  - on/off 토글 스위치, "서버와 함께 시작" 체크 반영
  - 웹서버 ▶/■ 버튼 (등록된 자유 명령 실행/중지)
  - 로그 버튼, ⋮ 메뉴 (편집 / 삭제)
- 앱 내 단축키: Ctrl+1~9 = n번째 터널 토글, Ctrl+Shift+1~9 = n번째 웹서버 토글

### 터널 생성 마법사

빈칸 채우기 단계형. 각 단계 하단에 실제 실행될 명령어를 미리보기로 표시.

1. **이름**: 터널 이름 입력 → `cloudflared tunnel create <이름>`
2. **도메인**: 서브도메인 입력 + 루트 도메인 (cert.pem에서 자동 감지, 수동 입력 가능) → `cloudflared tunnel route dns <이름> <서브도메인>.<도메인>`
3. **서비스**: 로컬 서비스 주소 (예: `http://localhost:8000`) → config-<이름>.yml 자동 생성 (tunnel id, credentials-file, ingress 규칙 + 404 fallback)
4. **서버 명령 (선택)**: 시작 명령 + 작업 폴더 + "터널과 함께 시작" 체크
5. **실행**: 명령 순차 실행, 각 단계 성공/실패 실시간 표시. 실패 시 해당 단계에서 멈추고 로그 표시, 이미 성공한 단계는 롤백 안내 (터널 삭제 버튼 제공)

### 터널 편집 / 삭제

- 편집: 서비스 주소(config.yml 수정), 서버 명령, 함께 시작 여부
- 삭제: 확인 다이얼로그 → 실행 중이면 중지 → `cloudflared tunnel delete <이름>` → config yml 삭제 → settings.json 메타 정리. DNS CNAME 레코드는 대시보드에서 수동 삭제하도록 링크 안내

### 로그 뷰어

- 터널별 창: cloudflared 로그 탭 + 웹서버 로그 탭
- 실시간 스트림, 오류(ERR/error) 라인 빨간색 하이라이트, 자동 스크롤(하단 고정 토글), 지우기 버튼
- 최근 2000라인 링버퍼

### 설정 화면

- SSH 프로필 CRUD: 이름, 호스트, 포트, 사용자, 키 파일 경로 / 연결 테스트 버튼
- cloudflared 경로 재정의 (기본: PATH 탐색)

## 상태 감지

- 프로세스 생존 = 기본 판정
- cloudflared stderr에서 `Registered tunnel connection` 감지 → 🟢, 시작 후 미감지 → 🟡, 프로세스 비정상 종료/연속 재연결 실패 → 🔴
- 원격 모드: SSH 채널 생존 + `pgrep -f "tunnel.*run.*<이름>"` 주기 폴링 (10초)

## 오류 처리

- cloudflared 명령 실패: exit code + stderr를 사용자 친화적 다이얼로그로 표시 (원문 로그 펼치기 가능)
- SSH 연결 실패/끊김: 상단에 배너 표시, 자동 재연결 1회 시도 후 수동 재시도 버튼
- 앱 종료 시 실행 중 프로세스 있으면 확인 다이얼로그 → 전체 정리 종료
- 이름 중복, 빈 입력 등은 마법사 단계에서 사전 검증

## 테스트

- core 단위 테스트 (pytest): cloudflared 출력 파싱(list JSON, 로그 라인 감지), config.yml 생성/수정, settings.json 스토어, 마법사 검증 로직 — Runner는 fake로 대체
- UI/통합: 실제 cloudflared로 생성→route→run→중지→삭제 전 과정 수동 검증 (체크리스트)

## 문서 산출물

- 본 스펙과 구현 계획, future-extensions 문서는 md와 동일 내용의 html 버전을 같은 이름으로 병행 생성 (html은 시각적 구성 강화). 개발 시 참조 기준은 md
