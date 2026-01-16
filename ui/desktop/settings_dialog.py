from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QFormLayout,
    QComboBox,
    QSpinBox,
    QDialogButtonBox,
)


class SettingsDialog(QDialog):
    def __init__(self, *, difficulty: str, total_rounds: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Game Settings")
        self.setModal(True)

        root = QVBoxLayout(self)
        form = QFormLayout()
        root.addLayout(form)

        self.difficulty_cb = QComboBox()
        self.difficulty_cb.addItems(["EASY", "MEDIUM", "HARD", "AXXELA"])
        self.difficulty_cb.setCurrentText((difficulty or "MEDIUM").upper())
        form.addRow("Difficulty", self.difficulty_cb)

        self.rounds_sb = QSpinBox()
        self.rounds_sb.setRange(1, 30)
        self.rounds_sb.setValue(int(total_rounds))
        form.addRow("Total rounds", self.rounds_sb)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def values(self) -> tuple[str, int]:
        return self.difficulty_cb.currentText(), int(self.rounds_sb.value())
