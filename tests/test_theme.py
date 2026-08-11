from app.ui.theme import ACCENT, build_qss, current_palette


def test_dark_palette_colors():
    qss = build_qss("dark")
    assert "#0f1117" in qss
    assert ACCENT in qss


def test_light_palette_colors():
    qss = build_qss("light")
    assert "#f5f6f8" in qss
    assert ACCENT in qss


def test_current_palette_fallback():
    assert current_palette("unknown") == current_palette("dark")
