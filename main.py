"""
main.py

Entry point for the desktop application.
Creates QApplication before importing UI components that may require QtWebEngine.
"""
import sys
import core_logic
from PySide6.QtWidgets import QApplication


def main():
    # Prepare fonts/resources
    core_logic.get_chinese_font()

    app = QApplication(sys.argv)
    # Import UI after QApplication to avoid QtWebEngine issues
    from ui_components import MainWindow
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
