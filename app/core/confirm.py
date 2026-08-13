"""삭제 확인/라우트 표시 이름 계산에 쓰이는 순수 함수 모음.

core 모듈은 PyQt6에 의존하지 않는다 - UI(다이얼로그, 위젯)는 이 함수들을
호출만 하고, 여기서는 문자열 비교/가공 로직만 다룬다.
"""
from __future__ import annotations


def tunnel_name_matches(input_text: str, tunnel_name: str) -> bool:
    """터널 삭제 확인 입력값이 터널 이름과 정확히 일치하는지 검사한다.

    앞뒤 공백만 trim하고, 대소문자는 구분한다.
    """
    return input_text.strip() == tunnel_name


def default_label_from_hostname(hostname: str) -> str:
    """hostname의 첫 라벨을 이름 대신 쓸 기본값으로 돌려준다.

    예: "app.example.com" -> "app". hostname이 비어 있으면 빈 문자열.
    """
    if not hostname:
        return ""
    return hostname.split(".", 1)[0]


def owner_label(owner_key: str) -> str:
    """터널을 만든 대상 키를 카드에 보여줄 사람이 읽는 이름으로 바꾼다.

    "local" -> "이 PC", "ssh:webPi" -> "webPi"(접두사 제거). 빈 값이거나
    알 수 없는 형태면 빈 문자열을 돌려준다 - 호출부는 이 경우 표시를 생략해야 한다.
    """
    if owner_key == "local":
        return "이 PC"
    if owner_key.startswith("ssh:"):
        return owner_key[len("ssh:"):]
    return ""


UNKNOWN_OWNER_LABEL = "기기 미확인"


def owner_group_label(owner_key: str) -> str:
    """터널 목록의 카테고리(기기) 머리글에 쓸 이름.

    owner_label()과 달리 절대 빈 문자열을 돌려주지 않는다 - 머리글은 그 아래
    터널들을 담는 상자라서 이름이 비면 정체 불명의 빈 줄이 된다. 소유 기기를
    아직 모르는 터널들은 UNKNOWN_OWNER_LABEL 아래로 모은다.
    """
    return owner_label(owner_key) or UNKNOWN_OWNER_LABEL


def group_by_owner(pairs, preferred_order):
    """(owner_key, 값) 목록을 owner_key별로 묶어 표시 순서대로 돌려준다.

    - preferred_order(예: ["local", "ssh:webPi"])에 있는 키를 그 순서대로 먼저,
    - 그 외 키는 처음 나타난 순서대로,
    - 소유 기기를 모르는 항목("")은 항상 마지막에 둔다.

    반환: [(owner_key, [값, ...]), ...]. 그룹 안의 순서는 입력 순서를 지킨다.
    """
    groups: dict[str, list] = {}
    for key, value in pairs:
        groups.setdefault(key, []).append(value)
    # preferred_order에 같은 키가 두 번 들어 있으면 그 그룹이 두 번 그려진다
    # (같은 이름의 SSH 프로필이 설정 파일에 중복으로 있는 경우 등).
    # 순서는 유지하면서 중복만 걷어낸다.
    seen_keys: set[str] = set()
    ordered = []
    for k in preferred_order:
        if k in groups and k not in seen_keys:
            seen_keys.add(k)
            ordered.append(k)
    ordered += [k for k in groups if k not in ordered and k != ""]
    if "" in groups:
        ordered.append("")
    return [(k, groups[k]) for k in ordered]


def route_display_label(label: str, hostname: str) -> tuple[str, bool]:
    """라우트 행에 보여줄 이름과, 그것이 사용자가 직접 입력한 것인지 여부.

    반환: (표시할 텍스트, is_placeholder). is_placeholder가 True면 label이
    비어 있어 hostname에서 유도된 값이라는 뜻 - 호출부는 이 경우 muted
    색으로 구분해 표시해야 한다.
    """
    label = (label or "").strip()
    if label:
        return label, False
    return default_label_from_hostname(hostname), True
