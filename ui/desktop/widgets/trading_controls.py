from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDoubleValidator, QIntValidator
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QGroupBox, QMessageBox
)


class TradingControls(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("Trading Controls")
        gl = QGridLayout(group)
        root.addWidget(group)

        # ---------- Validators (UX guidance only) ----------
        # Note: We still parse manually to be safe against locale issues
        self.px_validator = QDoubleValidator(0.0001, 1e9, 2)
        self.px_validator.setNotation(QDoubleValidator.Notation.StandardNotation)

        self.qty_validator = QIntValidator(1, 1_000_000)

        # ---------- Inputs ----------
        gl.addWidget(QLabel("Price:"), 0, 0)
        self.price_input = QLineEdit()
        self.price_input.setValidator(self.px_validator)
        self.price_input.setPlaceholderText("Limit Px")
        gl.addWidget(self.price_input, 0, 1)

        gl.addWidget(QLabel("Qty:"), 0, 2)
        self.qty_input = QLineEdit("1")
        self.qty_input.setValidator(self.qty_validator)
        gl.addWidget(self.qty_input, 0, 3)

        # ---------- Limit Orders ----------
        self.btn_buy = QPushButton("BUY LMT")
        self.btn_buy.setObjectName("BuyBtn")
        self.btn_buy.clicked.connect(self._on_buy)
        gl.addWidget(self.btn_buy, 1, 0, 1, 2)

        self.btn_sell = QPushButton("SELL LMT")
        self.btn_sell.setObjectName("SellBtn")
        self.btn_sell.clicked.connect(self._on_sell)
        gl.addWidget(self.btn_sell, 1, 2, 1, 2)

        # ---------- Market Orders ----------
        self.btn_lift = QPushButton("LIFT (Buy Mkt)")
        self.btn_lift.clicked.connect(self._on_lift)
        gl.addWidget(self.btn_lift, 2, 0, 1, 2)

        self.btn_hit = QPushButton("HIT (Sell Mkt)")
        self.btn_hit.clicked.connect(self._on_hit)
        gl.addWidget(self.btn_hit, 2, 2, 1, 2)

        # ---------- Market Making ----------
        gl.addWidget(QLabel("Bid / Ask:"), 3, 0)

        self.mm_bid = QLineEdit()
        self.mm_bid.setValidator(self.px_validator)
        self.mm_bid.setPlaceholderText("Bid Px")
        gl.addWidget(self.mm_bid, 3, 1)

        self.mm_ask = QLineEdit()
        self.mm_ask.setValidator(self.px_validator)
        self.mm_ask.setPlaceholderText("Ask Px")
        gl.addWidget(self.mm_ask, 3, 2)

        self.btn_quote = QPushButton("Quote")
        self.btn_quote.clicked.connect(self._on_quote)
        gl.addWidget(self.btn_quote, 3, 3)

        # ---------- Cancel ----------
        self.btn_cancel = QPushButton("CANCEL ALL ORDERS")
        self.btn_cancel.clicked.connect(self.controller.cancel_all)
        gl.addWidget(self.btn_cancel, 4, 0, 1, 4)

    # ======================================================================
    # Validation helpers (Centralized Logic)
    # ======================================================================

    def _get_valid_price(self, w: QLineEdit) -> float | None:
        """Parses price, ensuring it is a positive float."""
        text = w.text().strip()
        if not text:
            return None
        try:
            val = float(text)
            return val if val > 0 else None
        except ValueError:
            return None

    def _get_valid_qty(self) -> int | None:
        """Parses qty, ensuring it is a positive integer."""
        text = self.qty_input.text().strip()
        if not text:
            return None
        try:
            val = int(text)
            return val if val > 0 else None
        except ValueError:
            return None

    def _reject(self, msg: str):
        """Standardized rejection feedback."""
        # For a simulator, a popup is okay to teach discipline.
        # In a pro trading app, we'd log to a status bar to avoid blocking.
        QMessageBox.warning(self, "Order Rejected", msg)

    # ======================================================================
    # Actions
    # ======================================================================

    def _on_buy(self):
        p = self._get_valid_price(self.price_input)
        q = self._get_valid_qty()
        if p is None or q is None:
            self._reject("Please enter a valid positive Price and Quantity.")
            return
        self.controller.buy_limit(p, q)

    def _on_sell(self):
        p = self._get_valid_price(self.price_input)
        q = self._get_valid_qty()
        if p is None or q is None:
            self._reject("Please enter a valid positive Price and Quantity.")
            return
        self.controller.sell_limit(p, q)

    def _on_lift(self):
        q = self._get_valid_qty()
        if q is None:
            self._reject("Please enter a valid positive Quantity.")
            return
        self.controller.lift_ask(q)

    def _on_hit(self):
        q = self._get_valid_qty()
        if q is None:
            self._reject("Please enter a valid positive Quantity.")
            return
        self.controller.hit_bid(q)

    def _on_quote(self):
        b = self._get_valid_price(self.mm_bid)
        a = self._get_valid_price(self.mm_ask)
        q = self._get_valid_qty()

        if b is None or a is None or q is None:
            self._reject("Bid, Ask, and Quantity must all be valid positive numbers.")
            return

        if b >= a:
            self._reject(f"Bid ({b}) must be strictly lower than Ask ({a}).")
            return

        self.controller.make_market(b, a, q)

    def _on_cancel(self):
        self.controller.cancel_all()
