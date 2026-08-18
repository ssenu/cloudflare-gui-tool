"""카드(터널 · 서버 카테고리) 표시 순서 계산.

Qt에 의존하지 않는 순수 함수만 둔다. 드래그 이벤트 자체는 테스트하기 어렵지만
"어디에 놓았을 때 순서가 어떻게 되는가"는 전부 여기서 끝나므로, 화면 없이
검증할 수 있다.

카드 키는 종류를 접두사로 구분한다 - 터널 이름과 그룹 id가 우연히 같아도
서로 다른 카드여야 하기 때문이다.
"""

from __future__ import annotations

TUNNEL_PREFIX = "t:"
GROUP_PREFIX = "s:"


def card_key_group(tunnel_name: str) -> str:
    """터널 카드의 키."""
    return f"{TUNNEL_PREFIX}{tunnel_name}"


def card_key_server(group_id: str) -> str:
    """서버 카테고리 카드의 키."""
    return f"{GROUP_PREFIX}{group_id}"


def apply_order(keys: list[str], order: list[str]) -> list[str]:
    """keys를 저장된 order대로 정렬한다.

    - order에 있는 키를 그 순서대로 먼저 놓는다.
    - order에 없는 키(새로 만든 카드)는 keys의 원래 순서를 지키며 뒤에 붙는다.
    - order에만 있고 keys에는 없는 키(삭제된 카드)는 무시한다.
    """
    present = set(keys)
    seen: set[str] = set()
    result: list[str] = []
    for k in order:
        if k in present and k not in seen:
            seen.add(k)
            result.append(k)
    result += [k for k in keys if k not in seen]
    return result


def move_key(order: list[str], keys: list[str], key: str,
             before: str | None) -> list[str]:
    """key를 before 앞으로(before가 None이면 맨 뒤로) 옮긴 새 순서를 돌려준다.

    반환값은 현재 화면에 있는 카드(keys)만 담은 정규화된 목록이다 - 삭제된
    카드의 키가 설정 파일에 영원히 쌓이지 않게 하기 위함이다. key가 화면에
    없으면 순서를 바꾸지 않고 정규화만 한다.
    """
    current = apply_order(keys, order)
    if key not in current or key == before:
        return current
    current.remove(key)
    if before is None or before not in current:
        current.append(key)
    else:
        current.insert(current.index(before), key)
    return current


def drop_target(cards: list[tuple[str, int, int]], y: int) -> str | None:
    """드롭 지점 y가 "어느 카드 앞"인지 돌려준다. 맨 뒤면 None.

    cards는 (카드 키, 위쪽 y, 높이) 목록이며 화면에 보이는 순서여야 한다.
    카드의 위쪽 절반에 놓으면 그 카드 앞, 아래쪽 절반에 놓으면 다음 카드
    앞이다 - 목록 재정렬에서 흔한 규칙이고, 경계가 카드 사이가 아니라
    카드 한가운데라 손이 덜 정확해도 의도대로 들어간다.
    """
    for i, (key, top, height) in enumerate(cards):
        if y < top + height / 2:
            return key
        if y < top + height:
            return cards[i + 1][0] if i + 1 < len(cards) else None
    return None
