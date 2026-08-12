from app.core.confirm import (default_label_from_hostname, owner_label,
                              route_display_label, tunnel_name_matches)


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
