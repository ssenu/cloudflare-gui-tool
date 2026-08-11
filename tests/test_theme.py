from app.ui.theme import ACCENT, build_qss, current_palette


def test_dark_palette_colors():
    qss = build_qss("dark")
    assert "#0f1117" in qss
    assert ACCENT in qss


def test_light_palette_colors():
    qss = build_qss("light")
    assert "#f5f6f8" in qss
    assert "#3182F6" in qss
    assert "#f6821f" not in qss


def test_current_palette_fallback():
    assert current_palette("unknown") == current_palette("dark")


def test_dark_qss_has_orange_accent_only():
    qss = build_qss("dark")
    assert "#f6821f" in qss
    assert "#3182F6" not in qss


def test_build_qss_with_chevron_icon_adds_down_arrow_rule():
    qss = build_qss("dark", {"chevron": "C:/x/y.png"})
    assert "down-arrow" in qss
    assert "C:/x/y.png" in qss


def test_build_qss_without_icons_still_valid():
    qss = build_qss("dark")
    assert ACCENT in qss
    assert "down-arrow" not in qss
