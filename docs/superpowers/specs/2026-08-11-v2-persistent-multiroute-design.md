# v2 설계 — 상시 실행 · 다중 라우트 · 컨테이너 지원

날짜: 2026-08-11
상태: 승인 대기 (사용자 요청 7건을 반영한 설계)

## 배경

v1은 GUI가 cloudflared와 웹서버를 자식 프로세스로 붙잡고 있는 구조였다. 그래서 앱을 닫으면 터널도 함께 꺼지고, 노트북에서 라즈베리파이를 관리하다 SSH가 끊기면 원격 프로세스까지 죽었다. 또한 터널 하나에 사이트 하나만 연결할 수 있었고, 웹서버는 자유 명령 하나만 등록 가능해 컨테이너 운영에 맞지 않았다.

v2는 **프로세스 소유권을 GUI에서 대상 머신으로 옮긴다.** GUI는 실행 주체가 아니라 관찰자·조작자가 된다.

## 요청 사항과 대응

| # | 요청 | 대응 |
|---|------|------|
| 1 | 터널/서버 토글 분리 | 카드에 터널 토글 1개 + 라우트마다 서버 토글 1개 (그린 스위치 위젯) |
| 2 | 컨테이너 등록 | 서비스 종류를 `명령`/`도커 컴포즈`로 구분, 시작·정지 명령 쌍과 상태 판정 방식을 종류별로 다르게 |
| 3 | 한 터널에 여러 개 등록 | 터널이 라우트 목록을 가짐. config.yml에 ingress 규칙이 여러 개 생성됨 |
| 4 | GUI 종료해도 유지 + 재시작 시 복원 | 분리 실행(detached) + PID·로그 파일 기반 상태 관리 |
| 5 | 대상 드롭다운 화살표 디자인 | 직접 그린 셰브론 아이콘을 QSS에 연결 |
| 6 | 새로고침 아이콘 재작업 | 원호 + 화살촉을 QPainterPath로 다시 그림 |
| 7 | 삭제 빨간 글씨 | 메뉴의 삭제 항목과 확인 다이얼로그 버튼을 danger 색으로 |

## 핵심 구조 변경: 분리 실행과 파일 기반 상태

### 실행 방식

GUI는 프로세스를 **분리 실행**하고 즉시 손을 뗀다. 프로세스의 신원과 출력은 대상 머신의 파일에 남는다.

- 로컬(Windows): `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`으로 spawn, stdout/stderr을 로그 파일로 리다이렉트
- 원격(SSH): `cd <cwd> && setsid nohup <cmd> >> <log> 2>&1 < /dev/null & echo $!` — `nohup`으로 SIGHUP을 무시하고 `setsid`로 독립 프로세스 그룹을 만든다. 채널이 끊겨도 살아남는다.

### 실행 상태 디렉터리

대상 머신의 `~/.cloudflare-gui/run/` 아래에 단위별로 두 파일을 둔다.

```
tunnel-<터널명>.pid        tunnel-<터널명>.log
svc-<터널명>-<라우트ID>.pid  svc-<터널명>-<라우트ID>.log
```

PID 파일이 곧 "이 단위는 실행 중이어야 한다"는 선언이다. GUI는 매 폴링마다 PID 생존 여부를 확인해 실제 상태를 만든다. 즉 **상태는 메모리가 아니라 대상 머신의 디스크에 있다.** 그래서 GUI를 껐다 켜도, 다른 노트북에서 접속해도 같은 상태가 보인다.

### 상태 판정

| 단위 | 판정 방법 |
|------|-----------|
| 터널 | PID 파일 없음 → 중지 / PID 살아있고 로그에 `Registered tunnel connection` → 실행 중 / PID 살아있고 마커 없음 → 시작 중 / PID 죽음 → 오류 |
| 서비스(명령) | PID 파일 + 생존 여부 |
| 서비스(도커) | `docker compose ps -q`의 출력이 비어있지 않으면 실행 중 (PID 무관) |

PID 생존 확인은 라운드트립을 줄이기 위해 **일괄 조회**한다. 로컬은 ctypes `OpenProcess`, 원격은 한 번의 셸 명령으로 여러 PID를 검사한다.

### 종료 방식

- 로컬: `taskkill /PID <pid> /T /F` (자식까지)
- 원격: `kill -TERM -<pid>` (프로세스 그룹) → 유예 후 `kill -KILL -<pid>`, 실패 시 그룹 없이 재시도
- 도커 서비스: 등록된 정지 명령(`docker compose down`) 실행

정지가 성공하면 PID 파일을 지운다. 이것이 "사용자가 의도적으로 껐다"는 표시이며, 오류(비정상 종료)와 구분하는 근거다.

### 로그

파이프 대신 **로그 파일을 tail** 한다. 뷰어는 바이트 오프셋을 들고 있다가 증가분만 읽는다(로컬은 파일 시크, 원격은 SFTP 시크). 앱을 재시작해도 과거 로그를 그대로 볼 수 있고, 뷰어를 열지 않은 동안의 출력도 유실되지 않는다. 로그 파일이 무한히 커지지 않도록 시작 시 파일 크기가 5MB를 넘으면 새로 만든다(직전 것은 `.log.1`로 이동).

## 데이터 모델

```python
@dataclass
class ServiceSpec:
    kind: str = "command"        # "command" | "docker"
    start_cmd: str = ""
    stop_cmd: str = ""           # 비우면 PID 종료로 처리 (command 전용)
    cwd: str = ""
    autostart: bool = False      # 터널을 켤 때 함께 시작

@dataclass
class RouteMeta:
    id: str                      # 8자리 난수 hex, 생성 후 불변
    hostname: str = ""           # mysite.example.com
    service: str = ""            # http://localhost:8000
    server: ServiceSpec = field(default_factory=ServiceSpec)

@dataclass
class TunnelMeta:
    name: str
    routes: list[RouteMeta] = field(default_factory=list)
```

라우트 `id`는 hostname이 바뀌어도 PID·로그 파일과의 연결이 끊기지 않게 하기 위한 안정 키다.

### 기존 설정 마이그레이션

v1의 `TunnelMeta`는 `hostname`/`service`/`server_cmd`/`server_cwd`/`start_together`를 최상위에 두었다. 로드 시 `routes` 키가 없고 `hostname`·`service`·`server_cmd` 중 하나라도 값이 있으면 라우트 하나로 변환한다(서버 명령만 등록해 둔 터널의 설정도 잃지 않기 위해서다). `start_together`는 `server.autostart`로, `server_cmd`/`server_cwd`는 `kind="command"` 서비스로 옮긴다. 변환 후 곧바로 저장해 다음 실행부터는 새 형식만 남는다.

## config.yml 다중 ingress

```yaml
tunnel: <uuid>
credentials-file: <path>
ingress:
  - hostname: blog.example.com
    service: http://localhost:8000
  - hostname: api.example.com
    service: http://localhost:8001
  - service: http_status:404
```

`build_config(tunnel_id, credentials_file, routes)`가 라우트 목록을 받아 규칙을 만들고, 마지막 fallback은 항상 유지한다. `set_routes(text, routes)`는 기존 파일의 tunnel/credentials-file은 보존한 채 ingress만 교체한다. 라우트를 추가·삭제할 때마다 config를 다시 쓰고, **터널이 실행 중이면 재시작이 필요하다는 안내를 표시한다**(cloudflared는 config를 자동으로 다시 읽지 않는다).

## 화면 구성

### 터널 카드

```
● mysite                                    [터널 ●━]  로그  ⋮
   blog.example.com → localhost:8000        [서버 ━○]  로그
   api.example.com  → localhost:8001        [서버 ●━]  로그
                                            ＋ 라우트 추가
```

- 터널 토글과 서버 토글은 같은 스위치 위젯이지만 서로 독립적으로 동작한다. 터널을 켤 때 `autostart`가 켜진 서비스만 함께 시작한다.
- 서비스가 등록되지 않은 라우트는 서버 토글 대신 "서버 등록" 링크를 보여준다.
- 라우트 행의 ⋮ 메뉴: 편집 / 삭제.

### 라우트 편집 다이얼로그

hostname, 로컬 서비스 주소, 서비스 종류(명령 / 도커 컴포즈), 시작 명령, 정지 명령, 작업 폴더, 터널과 함께 시작 여부. 도커를 고르면 시작·정지 명령의 기본값을 `docker compose up -d` / `docker compose down`으로 채우고, 작업 폴더를 필수로 요구한다(compose 파일 위치).

### 종료 동작

`closeEvent`에서 아무것도 중지하지 않는다. 실행 중인 것이 있으면 "터널과 서버는 계속 실행됩니다"라는 정보성 안내만 상태 표시줄 수준으로 남긴다. 다시 실행하면 대상 머신의 run 디렉터리를 읽어 카드 상태를 복원한다.

## 아이콘·스타일 세부

- **콤보 화살표**: 테마 적용 시 셰브론 아이콘을 `%APPDATA%/CloudflareTunnelGUI/icons/`에 PNG로 굽고, QSS의 `QComboBox::down-arrow { image: url(...) }`로 연결한다. Qt 스타일시트는 메모리 픽스맵을 직접 받지 못하므로 파일 경유가 필요하다.
- **새로고침**: 300° 원호를 `QPainterPath.arcTo`로 그리고 끝점 접선 방향에 채워진 삼각형 화살촉을 붙인다. 선 끝은 둥글게(`RoundCap`).
- **삭제 빨간 글씨**: `QMenu`는 항목별 색을 QSS로 지정할 수 없으므로, 삭제 항목만 `QWidgetAction` + 라벨로 만들어 danger 색과 hover 배경을 직접 준다. 삭제 확인 다이얼로그의 실행 버튼도 danger 색으로 표시한다.

## 테스트 전략

- 코어 단위 테스트(pytest): 마이그레이션, 다중 ingress 생성·교체, PID 파일 파싱, 상태 판정 표의 각 분기, 도커 상태 판정, 로그 tail 오프셋 처리. Runner는 fake로 대체한다.
- 분리 실행은 실제 프로세스로 검증한다: 파이썬 자식 프로세스를 spawn_detached로 띄우고 부모가 죽어도 살아있는지, kill로 정리되는지.
- 원격(SSH) 경로와 도커 실행은 수동 체크리스트로 확인한다.

## v2에서 다루지 않는 것

- `docker run` 단독 컨테이너의 정확한 상태 판정 (compose 프로젝트만 지원)
- 라우트별 Cloudflare Access 설정
- 여러 대상 머신을 한 화면에서 동시에 모니터링
