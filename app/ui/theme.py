from app.core.process_mgr import TunnelState

STATE_COLORS = {
    TunnelState.STOPPED: "#888888",
    TunnelState.STARTING: "#d29922",
    TunnelState.RUNNING: "#3fb950",
    TunnelState.ERROR: "#f85149",
}

ACCENT = "#f6821f"

APP_QSS = """
QWidget { background: #0f1117; color: #e8eaf0;
          font-family: 'Segoe UI', 'Malgun Gothic'; font-size: 13px; }
QMainWindow, QDialog { background: #0f1117; }
QFrame#card { background: #181b24; border: 1px solid #2a2f3f; border-radius: 10px; }
QLabel#cardTitle { font-size: 15px; font-weight: 600; }
QLabel#cardSub { color: #9aa1b5; }
QPushButton { background: #1f2330; border: 1px solid #2a2f3f;
              border-radius: 6px; padding: 6px 14px; }
QPushButton:hover { border-color: #f6821f; }
QPushButton#primary { background: #f6821f; color: #ffffff; font-weight: 600;
                      border: none; }
QPushButton#primary:hover { background: #ff9a3d; }
QPushButton#danger { color: #f85149; }
QLineEdit, QComboBox, QSpinBox { background: #1f2330; border: 1px solid #2a2f3f;
    border-radius: 6px; padding: 6px 10px; selection-background-color: #f6821f; }
QLineEdit:focus, QComboBox:focus { border-color: #4c9aff; }
QPlainTextEdit { background: #0b0d12; border: 1px solid #2a2f3f;
    border-radius: 6px; font-family: 'Cascadia Code', Consolas; font-size: 12px; }
QScrollArea { border: none; }
QToolTip { background: #1f2330; color: #e8eaf0; border: 1px solid #2a2f3f; }
QMenu { background: #181b24; border: 1px solid #2a2f3f; }
QMenu::item:selected { background: #f6821f; color: white; }
QCheckBox::indicator { width: 16px; height: 16px; }
"""
