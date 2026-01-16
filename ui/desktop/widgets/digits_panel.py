from __future__ import annotations

from typing import Sequence, Any

from PyQt6.QtWidgets import QHBoxLayout, QLabel, QFrame
from PyQt6.QtCore import Qt


class DigitsPanel(QFrame):
    """
    Renders digits as boxes:
    - Unknown: "?"
    - Revealed: "0".."9"

    Important: avoid unpolish/polish spam every tick; only do it when state changes.
    """

    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(6, 6, 6, 6)
        self._layout.setSpacing(6)

        self._labels: list[QLabel] = []
        self._layout.addStretch()

    def update_digits(self, digits: Sequence[Any]) -> None:
        n = len(digits)

        while len(self._labels) < n:
            lbl = QLabel("?")
            lbl.setObjectName("DigitBox")  # keep stable
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setProperty("revealed", False)
            self._layout.insertWidget(len(self._labels), lbl)
            self._labels.append(lbl)

        for i, lbl in enumerate(self._labels):
            lbl.setVisible(i < n)

        for i in range(n):
            v = digits[i]
            is_unknown = (v is None) or (str(v) == "?")
            text = "?" if is_unknown else str(v)

            lbl = self._labels[i]
            if lbl.text() != text:
                lbl.setText(text)

            new_revealed = not is_unknown
            old_revealed = bool(lbl.property("revealed"))

            # Only trigger a re-style if state changed
            if new_revealed != old_revealed:
                lbl.setProperty("revealed", new_revealed)
                lbl.style().unpolish(lbl)
                lbl.style().polish(lbl)
