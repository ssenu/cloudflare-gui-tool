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
