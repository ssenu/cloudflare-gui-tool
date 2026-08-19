# 서버 카테고리 설계 — 터널 없이 서버만 모아 실행하기

작성일: 2026-08-19
상태: 승인됨 (구현 대기)

## 배경

지금 앱에서 도커 서버는 항상 터널에 딸린 라우트의 부속물이다. `RouteMeta.server`가
서버 실행 정보를 들고 있고, `RouteMeta`는 `TunnelMeta.routes` 안에만 존재한다.
라우트를 만들려면 서브도메인과 루트 도메인이 필수이고, 저장하는 순간 Cloudflare
DNS 레코드와 `config-<터널>.yml`의 ingress 항목이 만들어진다.

그래서 "외부에 열 필요 없는 백엔드 컨테이너만 띄우고 싶다"는 요구를 지금 구조로는
받아낼 수 없다. 도메인을 아무거나 지어내 라우트를 만들고 터널만 꺼두는 우회는
가능하지만, 쓰지도 않을 DNS 레코드가 남는다.

이 문서는 터널에 속하지 않는 **서버 카테고리**를 도입해 그 요구를 정면으로 받는
설계를 기술한다.

## 목표

- 상단 툴바 `＋터널 생성` 오른쪽의 `＋서버 카테고리` 버튼으로 서버 묶음을 만든다.
- 서버 카테고리 카드는 터널 카드와 같은 목록에 섞여 보이되, **터널 토글이 없다**.
- 카드 안에 서버를 여러 개 등록하고 각각 켜고 끌 수 있다. 도메인·DNS·ingress는
  일절 건드리지 않는다.
- 모든 카드(터널·서버) 왼쪽에 드래그 핸들을 두어 표시 순서를 사용자가 정한다.

## 하지 않는 것

- 그룹 사이의 서버 이동, 그룹 중첩
- 서버 카테고리의 부팅 시 자동 실행(systemd) — 터널의 `boot_autostart`에 해당하는 기능
- 기존 라우트를 서버 카테고리로 옮기는 마이그레이션

## 1. 데이터 모델

`app/core/store.py`

```python
@dataclass
class ServerGroupMeta:
    id: str                                  # new_route_id()와 같은 8자리 hex
    name: str                                # 카드 제목 (예: "백엔드")
    servers: list[RouteMeta] = field(default_factory=list)
```

`RouteMeta`를 그대로 재사용한다. 서버 항목에 필요한 것(`label`, `service`,
`server: ServiceSpec`)이 이미 전부 있고, 다른 점은 `hostname`이 항상 빈
문자열이라는 것뿐이다. 새 dataclass를 만들면 라우트 행과 서버 행이 공유하는
표시·실행 코드가 전부 두 벌이 된다.

`Settings`에 두 필드를 추가한다.

```python
server_groups: dict[str, list[ServerGroupMeta]] = field(default_factory=dict)
card_order: dict[str, list[str]] = field(default_factory=dict)
```

- `server_groups`의 바깥 키는 대상 키(`CommandRunner.name`: `"local"` 또는
  `"ssh:<프로필명>"`)다. `repos`와 같은 이유로 대상별로 나눈다 — 내 PC와 원격
  기기는 작업 폴더 경로도 컨테이너도 다르다. 접근자는 `repos_for`와 짝을 맞춰
  `server_groups_for(target_key)`로 둔다.
- `card_order`의 바깥 키는 카드가 묶이는 기기 그룹 키(`owner_key`)이고, 값은
  카드 키 목록이다. 카드 키는 터널이면 `t:<터널이름>`, 서버 카테고리면
  `s:<그룹id>`.

로드 시 검증은 기존 `_parse_repos_list` 패턴을 따른다: `id`/`name`이 비어 있지
않은 문자열이 아니면 그 항목만 건너뛴다. `servers`는 기존 `_parse_routes_list`를
그대로 쓴다. `card_order`는 문자열 키/문자열 리스트만 받아들인다. 두 필드 모두
옛 설정 파일에는 없으므로, 없으면 빈 dict로 로드되어야 한다.

## 2. 카드 순서 (`app/core/card_order.py`, 신규)

Qt에 의존하지 않는 순수 함수만 둔다. 드래그 자체는 테스트하기 어렵지만 순서
계산은 전부 여기서 끝나므로 단위 테스트가 가능하다.

```python
def apply_order(keys: list[str], order: list[str]) -> list[str]:
    """order에 있는 키를 그 순서대로 먼저, 나머지는 원래 순서대로 뒤에."""

def move_key(order: list[str], keys: list[str], key: str, before: str | None) -> list[str]:
    """key를 before 앞으로(before가 None이면 맨 뒤로) 옮긴 새 순서를 돌려준다.
    반환값은 현재 화면에 있는 키만 담은 정규화된 목록이다."""
```

`apply_order`는 새로 생긴 카드(순서 목록에 없는 것)를 뒤에 붙인다. 사라진 카드
키는 `move_key`가 정규화할 때 걸러진다 — 삭제된 터널의 키가 설정 파일에 영원히
쌓이지 않게 하기 위함이다.

## 3. 실행 단위와 프로세스 관리

### 유닛 이름

`RunRegistry.unit_service(owner, route_id)`는 지금 `svc-{owner}-{route_id}`를
만든다. 서버 카테고리는 `owner` 자리에 터널 이름 대신 `g<그룹id>`를 넘겨
`svc-g1a2b3c4-<서버id>`가 되게 한다. 두 번째 인자의 의미가 "터널 이름"에서
"소유 키"로 넓어질 뿐 형식은 그대로다. **기존 터널 서비스의 유닛 이름은 한 글자도
바뀌지 않으므로**, 지금 돌고 있는 서비스의 PID 파일과 로그 파일이 그대로 유효하다.

파라미터 이름을 `tunnel`에서 `owner`로 바꾸고, 그룹용 키를 만드는 헬퍼를 둔다.

```python
def group_owner_key(group_id: str) -> str:
    return f"g{group_id}"
```

### ProcessManager

`start_service` / `stop_service` / `service_running` / `service_pending` /
`docker_error` / `mark_service_pending` / `append_service_log` /
`log_path_for_service` / `cleanup_logs_for_service`의 첫 인자는 지금도 유닛 이름을
만드는 데만 쓰인다. 시그니처는 유지하고 파라미터 이름만 `owner`로 바꾼 뒤,
호출측이 터널 이름 또는 `group_owner_key(...)`를 넘긴다.

`refresh(tunnels)`는 `refresh(tunnels, groups=())`로 확장한다. 그룹의 서버도
도커 폴링 대상(`docker_units`)과 로그 tail 대상에 같은 방식으로 들어간다. 결과적으로
컨테이너 존재 + 실제 HTTP 응답 기반의 "실행 중" 판정, 전이 스피너, 배포 완료
알림이 서버 카테고리에서도 그대로 동작한다.

## 4. 화면

### 툴바

`＋터널 생성` 오른쪽에 `＋서버 카테고리` 버튼. 이름만 묻는 작은 입력 다이얼로그를
띄우고, 확인하면 현재 대상의 `server_groups`에 `ServerGroupMeta`를 추가한 뒤
저장하고 목록을 다시 그린다. 이름이 비어 있거나 같은 대상에 이미 있으면 거부한다.

### ServerGroupCard

터널 카드와 같은 `card` 스타일을 쓴다.

- 헤더: `☰`(드래그 핸들) · 이름 · `서버 N개` · (터널 토글 자리 비움) · `로그` · `⋮`
- `⋮` 메뉴: 모두 시작 / 모두 정지 / 이름 변경 / 삭제(위험 색)
- 본문: 서버 행 목록. 라우트 행과 열 구성이 같되 도메인 자리에 **장치 주소
  링크**가 들어간다 — `상태점 | 이름 | 장치 링크 | 주소 | [서버 토글] | ⋮`

  등록된 주소의 `localhost`는 **서버가 도는 기기**를 가리키므로, 앱을 보는
  사람의 PC에서는 그대로 열리지 않는다. 그래서 `localhost`(및 `127.0.0.1`,
  `0.0.0.0`) 자리에 그 장치의 주소를 끼운 링크를 만들어 누르면 브라우저로
  열리게 한다. 장치 주소는 원격이면 SSH로 접속하는 host, 내 PC면 LAN IP다
  (`app/core/service_url.py`). 만들 수 없으면 `(주소 없음)`과 이유 툴팁을
  보여준다 — 빈 칸만 두면 고장난 것처럼 보인다.
- 서버 행 `⋮` 메뉴: 배포(pull + 재시작) / 편집 / 삭제. "접속 확인"은 공개 주소가
  없으므로 넣지 않는다.
- 하단: `서버 추가` 버튼

카드 삭제는 실행 중인 서버가 있으면 먼저 정지하고 로그 파일을 정리한다 —
터널 삭제 경로와 같은 처리다.

### 목록 배치

`_render_cards`는 지금 계정에서 받아온 터널 목록만 그린다. 여기에 현재 대상의
서버 그룹을 더한다. 서버 그룹의 `owner_key`는 현재 대상 키다 — 터널과 달리 계정이
아니라 그 기기에만 존재하는 것이므로 자연스럽게 그 기기 머리글 아래에 들어간다.

기기 그룹 안의 순서는 `apply_order(그룹의 카드 키들, card_order[owner_key])`로
정한다.

`_render_signature`에는 서버 그룹 구성과 카드 순서가 들어가야 한다. 넣지 않으면
그룹을 추가하거나 순서를 바꿔도 1초 폴링이 "그릴 내용이 같다"고 판단해 화면이
갱신되지 않는다.

### 드래그로 순서 바꾸기

터널 카드와 서버 카테고리 카드 **모두** 헤더 맨 왼쪽에 `☰` 핸들을 둔다.

- 핸들을 누른 채 움직이면 `QDrag`가 시작되고, mime에는 카드 키(`t:`/`s:`)와
  `owner_key`를 담는다.
- 목록 컨테이너가 드롭을 받는다. 드래그 중인 y 좌표로 "어느 카드 앞에 놓을지"를
  정하고, 같은 `owner_key` 그룹 밖으로는 놓을 수 없다 — 터널이 어느 기기 소속인지는
  자격증명이 실제로 있는 위치라서 드래그로 바꿔서는 안 된다. 그룹 밖에서는 드롭을
  거부하고 커서로 알린다.
- 놓으면 `move_key`로 새 순서를 계산해 `card_order[owner_key]`에 저장하고 즉시
  다시 그린다.

드롭 위치 → 삽입 대상 계산은 `y좌표와 카드 위치 목록`을 받는 순수 함수로 빼서
Qt 이벤트 없이 테스트한다.

## 5. 서버 추가/편집 다이얼로그 (`app/ui/server_dialog.py`, 신규)

`route_dialog.py`는 절반 이상이 서브도메인 검증·DNS 레코드 생성·ingress 재작성
코드다. 거기에 "도메인 없는 모드" 분기를 넣으면 읽기 어려워지므로 별도 파일로
만든다.

입력 항목:

| 항목 | 설명 |
|---|---|
| 이름 | 카드에 표시할 이름. 필수 |
| 주소 | `http://localhost:8000`. 실행 중 판정(HTTP 응답 확인)에 쓰인다 |
| 종류 | 도커 컴포즈 / 일반 명령 |
| 작업 폴더 | 도커면 필수(compose 파일 위치) |
| 시작 명령 / 정지 명령 | 비우면 도커 기본값(`docker compose up --build -d` / `down`) |

검증과 경고는 기존 것을 재사용한다: `validate_service`(주소 형식),
`detect_service_port` + `port_mismatch`(도커 compose가 실제로 여는 포트와 입력한
주소의 포트가 다르면 저장 전에 경고), 그리고 실행 중인 서버의 종류·명령·폴더를
바꿀 때의 정지 확인(`route_dialog`의 I8 처리와 동일).

`ServiceSpec.autostart`("터널을 켤 때 함께 시작")는 붙을 터널이 없으므로 입력에서
제외한다. 그 자리는 그룹 `⋮`의 "모두 시작"이 대신한다.

## 6. 로그

`log_viewer`의 계층 탭 구조를 그대로 쓴다. 상위 탭이 그룹 이름, 하위 탭이 각
서버다. 터널 로그 탭만 없다.

## 7. 테스트

| 대상 | 내용 |
|---|---|
| `card_order` | `apply_order`의 신규 항목 뒤 배치, `move_key`의 앞/뒤 이동과 사라진 키 정규화, 드롭 인덱스 계산 |
| `store` | `server_groups`/`card_order` 저장·로드 왕복, 잘못된 항목 개별 스킵, 두 필드가 없는 옛 설정 파일 로드 |
| `process_mgr` | 그룹 유닛 이름, 그룹 서버 시작/정지, `refresh`의 그룹 도커 폴링, 기존 터널 유닛 이름 불변 |
| `server_dialog` | 필수 항목 검증, 도커 작업 폴더 강제, 포트 불일치 경고 |
| `main_window` | 그룹 카드 렌더링, 터널 토글 부재, `＋서버 카테고리` 동작, 순서 반영, 드롭 거부(다른 기기 그룹) |

기존 434개 테스트가 모두 통과해야 한다. 특히 `ProcessManager`의 파라미터 이름
변경이 키워드 인자로 호출하는 기존 테스트를 깨지 않는지 확인한다.

## 열린 위험

- **드래그 구현이 이 변경에서 가장 무거운 부분이다.** 카드는 `QVBoxLayout`에
  직접 들어가 있어 `QListWidget` 같은 기성 재정렬을 쓸 수 없다. 순서 계산을 순수
  함수로 빼서 위험을 좁히되, 드롭 처리 자체는 offscreen 테스트로 최소한만
  검증한다.
- `_render_signature`에 순서를 넣는 것을 빠뜨리면 "드래그했는데 1초 뒤 원래대로
  돌아온다"처럼 보인다. 구현 시 이 테스트를 먼저 쓴다.
