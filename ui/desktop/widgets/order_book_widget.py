# ui/desktop/widgets/order_book_widget.py
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView, QLabel
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

class OrderBookWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        
        # Labels
        self.spread_label = QLabel("Spread: -")
        self.layout.addWidget(self.spread_label)
        
        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Bid Qty", "Bid Px", "Ask Px", "Ask Qty"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.layout.addWidget(self.table)
        
    def update_book(self, bids, asks, spread):
        sp_text = f"{spread:.2f}" if spread is not None else "-"
        self.spread_label.setText(f"Spread: {sp_text}")
        
        # Limit depth display to 10
        depth = 10
        self.table.setRowCount(depth)
        
        # Clear content (not headers)
        self.table.clearContents()
        
        # Sort: Bids Descending, Asks Ascending
        # Bids come as list of tuples (px, qty)
        sorted_bids = sorted(bids, key=lambda x: x[0], reverse=True)[:depth]
        sorted_asks = sorted(asks, key=lambda x: x[0])[:depth]
        
        for r in range(depth):
            # BIDS
            if r < len(sorted_bids):
                px, qty = sorted_bids[r]
                item_qty = QTableWidgetItem(str(qty))
                item_px = QTableWidgetItem(f"{px:.2f}")
                
                item_px.setForeground(QColor("#4caf50")) # Green
                item_qty.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                
                self.table.setItem(r, 0, item_qty)
                self.table.setItem(r, 1, item_px)
            
            # ASKS
            if r < len(sorted_asks):
                px, qty = sorted_asks[r]
                item_px = QTableWidgetItem(f"{px:.2f}")
                item_qty = QTableWidgetItem(str(qty))
                
                item_px.setForeground(QColor("#f44336")) # Red
                item_qty.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                
                self.table.setItem(r, 2, item_px)
                self.table.setItem(r, 3, item_qty)
