from app.core.process_mgr import TunnelState

STATE_COLORS = {
    TunnelState.STOPPED: "#888888",
    TunnelState.STARTING: "#d29922",
    TunnelState.RUNNING: "#3fb950",
    TunnelState.ERROR: "#f85149",
}

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
        "accent": ACCENT,
        "accent2": ACCENT2,
        "log_bg": "#0b0d12",
        "danger_bg": "#3a1d1d",
        "danger": "#f85149",
        "preview_text": "#ffd28e",
    },
    "light": {
        "bg": "#f5f6f8",
        "panel": "#ffffff",
        "panel2": "#eceef2",
        "border": "#d5d9e0",
        "text": "#1c1e26",
        "muted": "#5a6072",
        "accent": ACCENT,
        "accent2": ACCENT2,
        "log_bg": "#ffffff",
        "danger_bg": "#fdecea",
        "danger": "#c62828",
        "preview_text": "#7a4a00",
    },
}


def current_palette(mode: str) -> dict:
    return PALETTES.get(mode, PALETTES["dark"])


def build_qss(mode: str) -> str:
    p = current_palette(mode)
    return f"""
QWidget {{ background: {p['bg']}; color: {p['text']};
          font-family: 'Segoe UI', 'Malgun Gothic'; font-size: 13px; }}
QMainWindow, QDialog {{ background: {p['bg']}; }}
QFrame#card {{ background: {p['panel']}; border: 1px solid {p['border']}; border-radius: 10px; }}
QLabel#cardTitle {{ font-size: 15px; font-weight: 600; }}
QLabel#cardSub {{ color: {p['muted']}; }}
QPushButton {{ background: {p['panel2']}; border: 1px solid {p['border']};
              border-radius: 6px; padding: 6px 14px; color: {p['text']}; }}
QPushButton:hover {{ border-color: {p['accent']}; }}
QPushButton#primary {{ background: {p['accent']}; color: #ffffff; font-weight: 600;
                      border: none; }}
QPushButton#primary:hover {{ background: #ff9a3d; }}
QPushButton#danger {{ color: {p['danger']}; }}
QLineEdit, QComboBox, QSpinBox {{ background: {p['panel2']}; border: 1px solid {p['border']};
    border-radius: 6px; padding: 6px 10px; selection-background-color: {p['accent']}; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {p['accent2']}; }}
QComboBox QAbstractItemView {{ background: {p['panel']}; color: {p['text']};
    border: 1px solid {p['border']}; selection-background-color: {p['accent']};
    selection-color: #ffffff; }}
QPlainTextEdit {{ background: {p['log_bg']}; border: 1px solid {p['border']};
    border-radius: 6px; font-family: 'Cascadia Code', Consolas; font-size: 12px;
    color: {p['text']}; }}
QScrollArea {{ border: none; }}
QToolTip {{ background: {p['panel2']}; color: {p['text']}; border: 1px solid {p['border']}; }}
QMenu {{ background: {p['panel']}; color: {p['text']}; border: 1px solid {p['border']}; }}
QMenu::item:selected {{ background: {p['accent']}; color: white; }}
QCheckBox::indicator {{ width: 16px; height: 16px; }}
QTabWidget::pane {{ border: 1px solid {p['border']}; background: {p['panel']}; }}
QTabBar::tab {{ background: {p['panel2']}; color: {p['text']}; padding: 6px 14px;
    border: 1px solid {p['border']}; border-bottom: none; }}
QTabBar::tab:selected {{ background: {p['panel']}; border-bottom: 2px solid {p['accent']}; }}
QListWidget {{ background: {p['panel2']}; border: 1px solid {p['border']};
    border-radius: 6px; color: {p['text']}; }}
QListWidget::item:selected {{ background: {p['accent']}; color: #ffffff; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; }}
QScrollBar:vertical {{ background: {p['panel']}; width: 10px; }}
QScrollBar::handle:vertical {{ background: {p['border']}; border-radius: 5px; }}
"""


# 하위 호환용 별칭 (임시 스크립트 등에서 사용)
APP_QSS = build_qss("dark")
