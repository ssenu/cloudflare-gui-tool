import os

from app.core.process_mgr import TunnelState

STATE_COLORS = {
    TunnelState.STOPPED: "#888888",
    TunnelState.STARTING: "#d29922",
    TunnelState.RUNNING: "#3fb950",
    TunnelState.ERROR: "#f85149",
}

# 하위 호환용 별칭 (dark 모드 기준, 임시 스크립트/테스트 등에서 사용)
ACCENT = "#f6821f"
ACCENT2 = "#4c9aff"

PALETTES = {
    "dark": {
        "bg": "#0f1117",
        "panel": "#181b24",
        "panel2": "#1f2330",
        "border": "#2a2f3f",
        "text": "#e8eaf0",
        "muted": "#9aa1b5",
        "accent": "#f6821f",
        "accent_hover": "#ff9a3d",
        "accent2": "#4c9aff",
        "log_bg": "#0b0d12",
        "danger_bg": "#3a1d1d",
        "danger": "#f85149",
        "preview_text": "#ffd28e",
        "on_accent": "#ffffff",
    },
    "light": {
        "bg": "#f5f6f8",
        "panel": "#ffffff",
        "panel2": "#eceef2",
        "border": "#d5d9e0",
        "text": "#1c1e26",
        "muted": "#5a6072",
        "accent": "#3182F6",
        "accent_hover": "#1B64DA",
        "accent2": "#1B64DA",
        "log_bg": "#ffffff",
        "danger_bg": "#fdecea",
        "danger": "#c62828",
        "preview_text": "#1B4F9C",
        "on_accent": "#ffffff",
    },
}


def current_palette(mode: str) -> dict:
    return PALETTES.get(mode, PALETTES["dark"])


def ensure_qss_icons(mode: str) -> dict[str, str]:
    """콤보박스 셰브론 아이콘을 PNG로 구워 경로를 돌려준다.

    Qt 스타일시트는 메모리 픽스맵을 직접 받지 못해 파일 경유가 필요하다.
    저장에 실패하면(권한 등) 조용히 무시하고 빈 dict를 돌려준다 —
    호출부는 이 경우 QSS를 기본 화살표로 폴백시킨다.
    """
    try:
        from app.ui.icons import make_icon

        p = current_palette(mode)
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        icon_dir = os.path.join(base, "CloudflareTunnelGUI", "icons")
        os.makedirs(icon_dir, exist_ok=True)
        path = os.path.join(icon_dir, f"chevron-{mode}.png")

        icon = make_icon("chevron_down", p["text"], size=24)
        pixmap = icon.pixmap(24, 24)
        if pixmap.isNull() or not pixmap.save(path, "PNG"):
            return {}

        return {"chevron": path.replace("\\", "/")}
    except OSError:
        return {}


def build_qss(mode: str, icon_paths: dict[str, str] | None = None) -> str:
    p = current_palette(mode)
    icon_paths = icon_paths or {}
    combo_arrow_qss = ""
    if icon_paths.get("chevron"):
        combo_arrow_qss = f"""
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url({icon_paths['chevron']}); width: 12px; height: 12px; }}
"""
    return f"""
QWidget {{ background: {p['bg']}; color: {p['text']};
          font-family: 'Segoe UI', 'Malgun Gothic'; font-size: 13px; }}
QMainWindow, QDialog {{ background: {p['bg']}; }}
QLabel {{ background: transparent; }}
QFrame#card {{ background: {p['panel']}; border: 1px solid {p['border']}; border-radius: 10px; }}
QLabel#cardTitle {{ font-size: 15px; font-weight: 600; }}
QLabel#cardSub {{ color: {p['muted']}; }}
QPushButton {{ background: {p['panel2']}; border: 1px solid {p['border']};
              border-radius: 6px; padding: 6px 14px; color: {p['text']}; }}
QPushButton:hover {{ border-color: {p['accent']}; }}
QPushButton#primary {{ background: {p['accent']}; color: {p['on_accent']}; font-weight: 600;
                      border: none; }}
QPushButton#primary:hover {{ background: {p['accent_hover']}; }}
QPushButton#danger {{ color: {p['danger']}; }}
QLineEdit, QComboBox, QSpinBox {{ background: {p['panel2']}; border: 1px solid {p['border']};
    border-radius: 6px; padding: 6px 10px; selection-background-color: {p['accent']}; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {p['accent2']}; }}
QComboBox QAbstractItemView {{ background: {p['panel']}; color: {p['text']};
    border: 1px solid {p['border']}; selection-background-color: {p['accent']};
    selection-color: {p['on_accent']}; }}
QPlainTextEdit {{ background: {p['log_bg']}; border: 1px solid {p['border']};
    border-radius: 6px; font-family: 'Cascadia Code', Consolas; font-size: 12px;
    color: {p['text']}; }}
QScrollArea {{ border: none; }}
QToolTip {{ background: {p['panel2']}; color: {p['text']}; border: 1px solid {p['border']}; }}
QMenu {{ background: {p['panel']}; color: {p['text']}; border: 1px solid {p['border']}; }}
QMenu::item:selected {{ background: {p['accent']}; color: {p['on_accent']}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; }}
QTabWidget::pane {{ border: 1px solid {p['border']}; background: {p['panel']}; }}
QTabBar::tab {{ background: {p['panel2']}; color: {p['text']}; padding: 6px 14px;
    border: 1px solid {p['border']}; border-bottom: none; }}
QTabBar::tab:selected {{ background: {p['panel']}; border-bottom: 2px solid {p['accent']}; }}
QListWidget {{ background: {p['panel2']}; border: 1px solid {p['border']};
    border-radius: 6px; color: {p['text']}; }}
QListWidget::item:selected {{ background: {p['accent']}; color: {p['on_accent']}; }}
QTableWidget, QTableView {{ background: {p['panel2']}; border: 1px solid {p['border']};
    border-radius: 6px; color: {p['text']}; gridline-color: {p['border']}; }}
QTableWidget::item, QTableView::item {{ padding: 4px 6px; }}
/* 선택 행 표시. 이 규칙이 없으면 Qt 기본 하이라이트가 우리 팔레트 위에서
   거의 보이지 않아 "선택됐는지 모르겠다"가 된다. 포커스를 잃어도(!active)
   같은 색을 유지해야 버튼을 누르러 간 사이 선택이 사라진 것처럼 보이지 않는다. */
QTableWidget::item:selected, QTableView::item:selected,
QTableWidget::item:selected:!active, QTableView::item:selected:!active {{
    background: {p['accent']}; color: {p['on_accent']}; }}
QHeaderView::section {{ background: {p['panel']}; color: {p['muted']};
    padding: 5px 6px; border: none; border-bottom: 1px solid {p['border']}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; }}
QScrollBar:vertical {{ background: {p['panel']}; width: 10px; }}
QScrollBar::handle:vertical {{ background: {p['border']}; border-radius: 5px; }}
{combo_arrow_qss}"""


# 하위 호환용 별칭 (임시 스크립트 등에서 사용)
APP_QSS = build_qss("dark")
