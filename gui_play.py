import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont
from ui.desktop.main_window import MainWindow

def main() -> None:
    app = QApplication(sys.argv)
    
    # Set default app font using points (not pixels) to avoid QFont warnings
    font = QFont("Segoe UI", 10)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(font)

    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
