"""Light professional theme, modelled on mainstream CAE desktops."""

THEME = """
QMainWindow { background: #eef0f3; }
QMenuBar { background: #f7f8fa; padding: 2px 4px; }
QMenuBar::item { padding: 4px 10px; border-radius: 3px; }
QMenuBar::item:selected { background: #dcebf7; }
QToolBar { background: #f7f8fa; border-bottom: 1px solid #cfd6dd; padding: 4px; spacing: 4px; }
QToolButton { padding: 5px 10px; border: 1px solid transparent; border-radius: 4px; }
QToolButton:hover { background: #e3eef8; border-color: #b9d2ea; }
QDockWidget { border: 1px solid #cfd6dd; }
QDockWidget::title { background: #e6eaef; padding: 5px 8px; font-weight: bold; }
QTreeWidget { background: white; border: none; outline: 0; }
QTreeWidget::item { padding: 3px 2px; }
QTreeWidget::item:selected { background: #cfe4f7; color: #10243a; }
QWidget#propsPanel { background: white; }
QLabel#panelHint { color: #5a6672; }
QPushButton { background: #f7f8fa; border: 1px solid #b9c2cb; border-radius: 4px; padding: 5px 12px; }
QPushButton:hover { background: #e3eef8; }
QPushButton:disabled { color: #9aa4ad; background: #eef0f3; }
QPushButton#primary { background: #1668b4; border-color: #1668b4; color: white; font-weight: bold; }
QPushButton#primary:hover { background: #1c7ad1; }
QToolButton#primary { background: #1668b4; color: white; font-weight: bold; border-radius: 4px; }
QToolButton#primary:hover { background: #1c7ad1; }
QTextEdit#logView { background: #fbfcfd; border: none; font-family: monospace; }
QLabel.chip { background: #dde3e9; color: #5a6672; border-radius: 9px; padding: 3px 10px; }
QLabel.chip[active="true"] { background: #1668b4; color: white; }
QStatusBar { background: #e6eaef; }
QDoubleSpinBox, QLineEdit { background: white; border: 1px solid #b9c2cb; border-radius: 3px; padding: 3px 6px; }
"""
