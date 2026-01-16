# ui/desktop/widgets/position_panel.py
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QVBoxLayout, QFrame

class StatBox(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("background-color: #252526; border-radius: 4px;")
        l = QVBoxLayout(self)
        l.setContentsMargins(5,5,5,5)
        
        self.title_lbl = QLabel(title)
        self.title_lbl.setStyleSheet("color: #888; font-size: 10px;")
        l.addWidget(self.title_lbl)
        
        self.value_lbl = QLabel("-")
        self.value_lbl.setStyleSheet("font-size: 16px; font-weight: bold;")
        l.addWidget(self.value_lbl)

    def set_value(self, text, color=None):
        self.value_lbl.setText(text)
        if color:
            self.value_lbl.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {color};")
        else:
            self.value_lbl.setStyleSheet("font-size: 16px; font-weight: bold; color: #e0e0e0;")

class PositionPanel(QWidget):
    def __init__(self):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        
        self.pnl_box = StatBox("Unrealized PnL")
        self.pos_box = StatBox("Position")
        self.cash_box = StatBox("Cash")
        self.fees_box = StatBox("Fees")
        
        layout.addWidget(self.pnl_box)
        layout.addWidget(self.pos_box)
        layout.addWidget(self.cash_box)
        layout.addWidget(self.fees_box)
        
    def update_stats(self, pnl, pos, cash, fees):
        color = "#4caf50" if pnl > 0 else "#f44336" if pnl < 0 else "#e0e0e0"
        self.pnl_box.set_value(f"${pnl:.2f}", color)
        
        self.pos_box.set_value(str(pos))
        self.cash_box.set_value(f"${cash:.2f}")
        self.fees_box.set_value(f"${fees:.2f}")
