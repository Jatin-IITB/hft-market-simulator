from __future__ import annotations

from typing import Iterable, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem


class LeaderboardDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Leaderboard")
        self.setModal(False)
        self.resize(520, 320)

        root = QVBoxLayout(self)

        self.header = QLabel("Intermission Leaderboard")
        self.header.setAlignment(Qt.AlignmentFlag.AlignLeft)
        # Use pt, not px
        self.header.setStyleSheet("font-size: 12pt; font-weight: bold;")
        root.addWidget(self.header)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Rank", "Trader", "PnL", "Pos"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        root.addWidget(self.table)

    def update_header(self, *, round_no: int, seconds_left: int) -> None:
        self.header.setText(f"Intermission • After Round {round_no} • Next round in {seconds_left}s")

    def update_leaderboard(self, rows: Iterable[Tuple[str, float, int]]) -> None:
        data = list(rows)
        self.table.setRowCount(len(data))

        for i, (name, pnl, pos) in enumerate(data, start=1):
            self._set_item(i - 1, 0, str(i), align=Qt.AlignmentFlag.AlignRight)
            self._set_item(i - 1, 1, str(name))
            self._set_item(i - 1, 2, f"{pnl:.2f}", align=Qt.AlignmentFlag.AlignRight)
            self._set_item(i - 1, 3, str(pos), align=Qt.AlignmentFlag.AlignRight)

        self.table.resizeColumnsToContents()

    def _set_item(self, r: int, c: int, text: str, align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft) -> None:
        it = QTableWidgetItem(text)
        it.setTextAlignment(int(align | Qt.AlignmentFlag.AlignVCenter))
        self.table.setItem(r, c, it)
