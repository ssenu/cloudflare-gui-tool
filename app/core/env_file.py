"""프로젝트의 .env 파일(KEY=VALUE)을 읽고 쓰는 순수 로직.

왜 docker-compose.yml이 아니라 .env를 다루는가:

1. compose 파일을 YAML로 파싱해서 다시 쓰면 주석이 전부 사라진다. 사용자가
   적어둔 "볼륨을 안 붙이면 데이터가 사라진다" 같은 경고가 조용히 지워지는
   것은 받아들일 수 없다.
2. compose 파일은 보통 git으로 추적된다. 앱이 그 파일을 고치면 대상 머신에
   로컬 수정이 생겨 다음 `git pull --ff-only`가 거부된다. .env는 관례적으로
   .gitignore에 들어가므로 그럴 일이 없다.

그래서 포트 같은 배포처마다 다른 값은 compose에 ``${HOST_PORT:-8000}``으로
두고, 이 모듈이 .env의 그 한 줄만 바꾼다.

편집은 "줄 단위 보존"이 원칙이다 - 아는 키만 제자리에서 갈아끼우고, 주석과
빈 줄, 모르는 줄은 원문 그대로 남긴다.
"""
from __future__ import annotations

import re

# .env 키 규칙. 셸 변수와 같게 잡는다(숫자로 시작 불가).
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# 값에 이 문자가 있으면 따옴표로 감싼다. 공백은 compose가 그대로 읽지만
# '#'은 주석 시작으로 오해될 수 있다.
_NEEDS_QUOTE_RE = re.compile(r'[#\s"\']')

# 화면에 값을 가려서 보여줄 키. 부분 일치로 판정한다.
_SECRET_HINTS = ("PASSWORD", "SECRET", "TOKEN", "APIKEY", "API_KEY",
                 "PRIVATE", "CREDENTIAL")

# compose에서 호스트 포트를 뽑아 쓸 때 약속한 키 이름.
HOST_PORT_KEY = "HOST_PORT"


def is_secret_key(key: str) -> bool:
    """값을 가려서 보여줘야 하는 키인지 판정한다."""
    upper = key.upper()
    return any(hint in upper for hint in _SECRET_HINTS)


def validate_env_key(key: str) -> str:
    """키 이름을 검증한다. 오류 메시지 또는 빈 문자열."""
    key = key.strip()
    if not key:
        return "이름을 입력하세요"
    if not _KEY_RE.match(key):
        return "영문/숫자/밑줄만 쓸 수 있고 숫자로 시작할 수 없습니다"
    return ""


def validate_port(value: str) -> str:
    """호스트 포트 값을 검증한다. 오류 메시지 또는 빈 문자열.

    1024 미만은 리눅스에서 root 권한이 있어야 열 수 있어, 도커가 아니라
    사용자 권한으로 도는 구성에서는 사실상 못 쓴다. 미리 막아준다.
    """
    value = value.strip()
    if not value:
        return "포트를 입력하세요"
    if not value.isdigit():
        return "숫자만 입력하세요"
    port = int(value)
    if port < 1024:
        return "1024 이상을 쓰세요 (그 아래는 관리자 권한이 필요합니다)"
    if port > 65535:
        return "65535 이하여야 합니다"
    return ""


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _quote(value: str) -> str:
    if value and _NEEDS_QUOTE_RE.search(value):
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _split_line(line: str) -> tuple[str, str] | None:
    """KEY=VALUE 줄이면 (키, 값), 아니면 None(주석/빈 줄/이상한 줄)."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, _, raw = stripped.partition("=")
    key = key.strip()
    if not _KEY_RE.match(key):
        return None
    return key, _unquote(raw)


class EnvFile:
    """줄 순서와 주석을 보존하는 .env 편집기.

    같은 키가 여러 번 나오면 마지막 것이 이긴다(docker compose와 같은 규칙).
    set()은 마지막 줄을 고치고 나머지는 건드리지 않는다.
    """

    def __init__(self, text: str = ""):
        # splitlines()는 마지막 개행 여부를 잃는다. to_text()에서 항상 개행으로
        # 끝나게 만들어 정규화한다(편집 때마다 diff가 흔들리지 않도록).
        self.lines: list[str] = text.splitlines() if text else []

    @classmethod
    def load(cls, runner, path: str) -> "EnvFile":
        """대상 머신에서 읽어온다. 파일이 없으면 빈 EnvFile."""
        if not runner.file_exists(path):
            return cls("")
        return cls(runner.read_file(path))

    def items(self) -> list[tuple[str, str]]:
        """정의된 키-값을 파일에 나온 순서대로 돌려준다(중복 키는 마지막 값)."""
        seen: dict[str, str] = {}
        order: list[str] = []
        for line in self.lines:
            pair = _split_line(line)
            if pair is None:
                continue
            key, value = pair
            if key not in seen:
                order.append(key)
            seen[key] = value
        return [(k, seen[k]) for k in order]

    def get(self, key: str, default: str = "") -> str:
        for k, v in self.items():
            if k == key:
                return v
        return default

    def set(self, key: str, value: str) -> None:
        """기존 줄을 제자리에서 바꾸고, 없으면 끝에 추가한다."""
        new_line = f"{key}={_quote(value)}"
        last = -1
        for i, line in enumerate(self.lines):
            pair = _split_line(line)
            if pair is not None and pair[0] == key:
                last = i
        if last >= 0:
            self.lines[last] = new_line
        else:
            self.lines.append(new_line)

    def remove(self, key: str) -> None:
        """그 키를 정의한 줄을 모두 지운다(주석은 그대로 둔다)."""
        kept = []
        for line in self.lines:
            pair = _split_line(line)
            if pair is not None and pair[0] == key:
                continue
            kept.append(line)
        self.lines = kept

    def to_text(self) -> str:
        if not self.lines:
            return ""
        return "\n".join(self.lines) + "\n"


def compose_host_port(text: str) -> str:
    """docker-compose.yml 본문에서 호스트 포트를 찾아 돌려준다(없으면 "").

    ``- "127.0.0.1:8000:8000"`` 처럼 흔한 형태만 본다. YAML로 파싱하지 않는
    이유는 이 값이 '보여주기와 자동 채움'에만 쓰이고, 틀렸을 때 조용히
    잘못된 파일을 쓰는 것보다 못 찾는 편이 안전하기 때문이다.

    ``${HOST_PORT:-8000}`` 형태면 기본값(8000)을 돌려준다 - 실제 값은
    .env가 정하므로 호출부가 EnvFile.get(HOST_PORT_KEY)를 우선 본다.
    """
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("-"):
            continue
        body = line.lstrip("- ").strip().strip("\"'")
        if ":" not in body:
            continue
        # ${HOST_PORT:-8000} 안에도 콜론이 있어 split이 어긋난다. 변수 형태를
        # 먼저 찾아 그 기본값을 쓰고, 없을 때만 콜론으로 나눈다.
        m = re.search(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-(\d+)\}", body)
        if m:
            return m.group(1)
        # 맨 앞이 호스트 IP면 떼어낸다 (127.0.0.1:8000:8000)
        parts = body.split(":")
        if len(parts) == 3:
            host_part = parts[1]
        elif len(parts) == 2:
            host_part = parts[0]
        else:
            continue
        if host_part.isdigit():
            return host_part
    return ""
