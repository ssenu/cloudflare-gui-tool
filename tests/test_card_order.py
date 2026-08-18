from app.core.card_order import (apply_order, card_key_group, card_key_server,
                                 drop_target, move_key)


# ---- 카드 키 ----

def test_card_keys_are_namespaced():
    """터널 이름과 그룹 id가 우연히 같아도 서로 다른 카드로 구분되어야 한다."""
    assert card_key_group("abcd") != card_key_server("abcd")


# ---- apply_order ----

def test_apply_order_follows_saved_order():
    keys = ["t:a", "t:b", "s:1"]
    assert apply_order(keys, ["s:1", "t:b", "t:a"]) == ["s:1", "t:b", "t:a"]


def test_apply_order_puts_new_cards_at_the_end():
    """저장된 순서에 없는 카드(새로 만든 것)는 원래 순서를 지키며 뒤에 붙는다."""
    keys = ["t:a", "t:new1", "s:1", "t:new2"]
    assert apply_order(keys, ["s:1", "t:a"]) == ["s:1", "t:a", "t:new1", "t:new2"]


def test_apply_order_ignores_keys_that_no_longer_exist():
    """삭제된 터널이 저장된 순서에 남아 있어도 빈 자리를 만들지 않는다."""
    assert apply_order(["t:a", "t:b"], ["t:gone", "t:b", "t:a"]) == ["t:b", "t:a"]


def test_apply_order_with_empty_order_keeps_input_order():
    assert apply_order(["t:a", "s:1"], []) == ["t:a", "s:1"]


# ---- move_key ----

def test_move_key_before_another_card():
    order = move_key([], ["t:a", "t:b", "s:1"], "s:1", before="t:a")
    assert order == ["s:1", "t:a", "t:b"]


def test_move_key_to_the_end_when_before_is_none():
    order = move_key([], ["t:a", "t:b", "s:1"], "t:a", before=None)
    assert order == ["t:b", "s:1", "t:a"]


def test_move_key_normalizes_away_stale_keys():
    """저장된 순서에 남은 삭제된 카드 키는 이동할 때 정리된다."""
    order = move_key(["t:gone", "t:a", "t:b"], ["t:a", "t:b"], "t:b", before="t:a")
    assert order == ["t:b", "t:a"]


def test_move_key_is_a_noop_when_key_is_not_visible():
    order = move_key(["t:a"], ["t:a", "t:b"], "s:없음", before="t:a")
    assert order == ["t:a", "t:b"]


def test_move_key_onto_itself_keeps_order():
    order = move_key([], ["t:a", "t:b"], "t:a", before="t:a")
    assert order == ["t:a", "t:b"]


# ---- drop_target ----

def test_drop_target_picks_the_card_whose_upper_half_contains_y():
    # (키, 위쪽 y, 높이)
    cards = [("t:a", 0, 100), ("t:b", 100, 100), ("s:1", 200, 100)]
    assert drop_target(cards, 20) == "t:a"      # 첫 카드 위쪽 절반 -> 그 앞에
    assert drop_target(cards, 120) == "t:b"
    assert drop_target(cards, 170) == "s:1"     # t:b의 아래쪽 절반 -> 다음 카드 앞


def test_drop_target_below_everything_is_the_end():
    cards = [("t:a", 0, 100), ("t:b", 100, 100)]
    assert drop_target(cards, 900) is None


def test_drop_target_with_no_cards_is_the_end():
    assert drop_target([], 10) is None
