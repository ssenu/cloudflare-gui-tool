from app.core.confirm import (default_label_from_hostname, group_by_owner,
                              owner_group_label, owner_label,
                              route_display_label, tunnel_name_matches)


def test_owner_group_label_falls_back_for_unknown():
    assert owner_group_label("local") == "이 PC"
    assert owner_group_label("ssh:webPi") == "webPi"
    assert owner_group_label("") == "기기 미확인"
    assert owner_group_label("garbage") == "기기 미확인"


def test_group_by_owner_orders_preferred_first_then_appearance():
    pairs = [("ssh:b", "b1"), ("", "x"), ("local", "l1"),
             ("ssh:b", "b2"), ("ssh:z", "z1")]
    assert group_by_owner(pairs, ["local", "ssh:b"]) == [
        ("local", ["l1"]),
        ("ssh:b", ["b1", "b2"]),   # 그룹 안에서는 입력 순서 유지
        ("ssh:z", ["z1"]),         # 순서 목록에 없는 키는 처음 나온 순서대로
        ("", ["x"]),               # 소유 기기 미확인은 항상 마지막
    ]


def test_group_by_owner_empty():
    assert group_by_owner([], ["local"]) == []


def test_tunnel_name_matches_exact():
    assert tunnel_name_matches("mysite", "mysite") is True


def test_tunnel_name_matches_trims_whitespace():
    assert tunnel_name_matches("  mysite  ", "mysite") is True


def test_tunnel_name_matches_is_case_sensitive():
    assert tunnel_name_matches("MySite", "mysite") is False


def test_tunnel_name_matches_rejects_partial():
    assert tunnel_name_matches("mysite2", "mysite") is False
    assert tunnel_name_matches("mysit", "mysite") is False


def test_tunnel_name_matches_empty_input():
    assert tunnel_name_matches("", "mysite") is False


def test_default_label_from_hostname():
    assert default_label_from_hostname("app.example.com") == "app"
    assert default_label_from_hostname("app") == "app"
    assert default_label_from_hostname("") == ""


def test_route_display_label_uses_explicit_label():
    text, is_placeholder = route_display_label("My App", "app.example.com")
    assert text == "My App"
    assert is_placeholder is False


def test_route_display_label_falls_back_to_hostname_first_label():
    text, is_placeholder = route_display_label("", "app.example.com")
    assert text == "app"
    assert is_placeholder is True


def test_route_display_label_strips_whitespace_label():
    text, is_placeholder = route_display_label("   ", "app.example.com")
    assert text == "app"
    assert is_placeholder is True


def test_owner_label_local():
    assert owner_label("local") == "이 PC"


def test_owner_label_ssh_strips_prefix():
    assert owner_label("ssh:webPi") == "webPi"


def test_owner_label_empty_is_empty():
    assert owner_label("") == ""


def test_owner_label_unknown_is_empty():
    assert owner_label("something-else") == ""


def test_owner_label_ssh_prefix_only_is_empty():
    assert owner_label("ssh:") == ""
