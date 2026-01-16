from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QSettings, QByteArray
from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QListWidget,
    QSplitter,
    QMessageBox,
    QDialog
)

from ui.desktop.controller import MarketController
from ui.desktop.settings_dialog import SettingsDialog

from ui.desktop.widgets.digits_panel import DigitsPanel
from ui.desktop.widgets.order_book_widget import OrderBookWidget
from ui.desktop.widgets.position_panel import PositionPanel
from ui.desktop.widgets.trading_controls import TradingControls
from ui.desktop.widgets.leaderboard_dialog import LeaderboardDialog
from ui.desktop.widgets.game_over_dialog import GameOverDialog

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Axxela Market Simulator (Pro)")
        self.resize(1100, 750)

        self._settings = QSettings("axxela", "axxela_market_sim")
        self._difficulty = self._settings.value("game/difficulty", "MEDIUM", type=str)
        self._total_rounds = int(self._settings.value("game/total_rounds", 6))

        qss_path = Path(__file__).with_name("styles.qss")
        if qss_path.exists():
            self.setStyleSheet(qss_path.read_text(encoding="utf-8"))

        self._last_snapshot = None
        self._leaderboard_round_shown: int | None = None
        self._leaderboard_dlg: LeaderboardDialog | None = None
        self._game_over_shown = False
        self.controller = MarketController(
            difficulty_name=self._difficulty,
            total_rounds=self._total_rounds,
        )
        self.controller.snapshot_updated.connect(self.render_snapshot)
        self.controller.error_raised.connect(self._on_error)

        self._build_menu()
        self._build_ui()

        self.controller.start_loop()

        geo = self._settings.value("ui/geometry", QByteArray(), type=QByteArray)
        if geo and not geo.isEmpty():
            self.restoreGeometry(geo)

        self._maybe_first_run_prompt()

    # ---------- UI ----------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        header = QHBoxLayout()
        self.round_label = QLabel("Round: -/-")
        self.timer_label = QLabel("T: -")
        # Use pt, not px to avoid QFont warnings
        self.timer_label.setStyleSheet("font-size: 14pt; font-weight: bold; color: #ff9800;")

        # Start button (shown only when NOT_STARTED)
        self.btn_start = QPushButton("START GAME")
        self.btn_start.setObjectName("StartBtn")  # New object name
        self.btn_start.setMinimumWidth(160)
        self.btn_start.clicked.connect(self.controller.start_game)

        # Reset/Exit buttons (shown during game)
        self.btn_reset = QPushButton("RESET")
        self.btn_reset.setMinimumWidth(120)
        self.btn_reset.clicked.connect(self._reset_same_settings)
        self.btn_reset.setVisible(False)  # Hidden initially

        self.btn_exit = QPushButton("EXIT")
        self.btn_exit.setMinimumWidth(120)
        self.btn_exit.clicked.connect(self.close)
        self.btn_exit.setVisible(False)   # Hidden initially

        header.addWidget(self.round_label)
        header.addStretch()
        header.addWidget(self.timer_label)
        header.addStretch()
        header.addWidget(self.btn_start)
        header.addWidget(self.btn_reset)
        header.addWidget(self.btn_exit)
        main_layout.addLayout(header)

        self.digits_panel = DigitsPanel()
        main_layout.addWidget(self.digits_panel)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)

        self.pos_panel = PositionPanel()
        left_layout.addWidget(self.pos_panel)

        self.controls = TradingControls(self.controller)
        left_layout.addWidget(self.controls)

        left_layout.addWidget(QLabel("Event Log"))
        self.log_list = QListWidget()
        # Use pt here too
        self.log_list.setStyleSheet("font-family: Consolas; font-size: 10pt;")
        left_layout.addWidget(self.log_list)

        splitter.addWidget(left_widget)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        self.book_widget = OrderBookWidget()
        right_layout.addWidget(self.book_widget)

        right_layout.addWidget(QLabel("My Trades"))
        self.trades_list = QListWidget()
        self.trades_list.setMaximumHeight(150)
        right_layout.addWidget(self.trades_list)

        splitter.addWidget(right_widget)
        splitter.setSizes([450, 650])

        main_layout.addWidget(splitter)

    # ---------- Menu ----------

    def _build_menu(self) -> None:
        mb = self.menuBar()
        game = mb.addMenu("Game")

        act_new = game.addAction("New Game…")
        act_new.triggered.connect(self._new_game)

        game.addSeparator()

        act_quit = game.addAction("Quit")
        act_quit.triggered.connect(self.close)

    # ---------- Settings flows ----------

    def _maybe_first_run_prompt(self) -> None:
        if self._settings.contains("game/difficulty") and self._settings.contains("game/total_rounds"):
            return
        self._new_game(first_run=True)

    def _new_game(self, first_run: bool = False) -> None:
        self._game_over_shown = False
        if not first_run and self._last_snapshot is not None:
            st = getattr(self._last_snapshot, "game_state", None)
            st_val = str(getattr(st, "value", st)).lower()
            # If game is running (not not_started), confirm reset
            if st_val not in ("notstarted", "not_started", "gamecomplete", "game_complete"):
                resp = QMessageBox.question(
                    self,
                    "New Game",
                    "This will discard the current session and start a new one.\nContinue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if resp != QMessageBox.StandardButton.Yes:
                    return

        dlg = SettingsDialog(difficulty=self._difficulty, total_rounds=self._total_rounds, parent=self)
        if dlg.exec() != QDialog.accepted:
            return

        difficulty, total_rounds = dlg.values()
        self._difficulty = (difficulty or "MEDIUM").upper()
        self._total_rounds = int(total_rounds)

        self._settings.setValue("game/difficulty", self._difficulty)
        self._settings.setValue("game/total_rounds", self._total_rounds)

        self._leaderboard_round_shown = None
        if self._leaderboard_dlg is not None:
            self._leaderboard_dlg.close()
            self._leaderboard_dlg = None

        self.controller.reset_game(difficulty_name=self._difficulty, total_rounds=self._total_rounds, start_loop=True)

    def _reset_same_settings(self) -> None:
        self._game_over_shown =False
        resp = QMessageBox.question(
            self,
            "Reset",
            "Reset the current game with the same settings?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return

        self._leaderboard_round_shown = None
        if self._leaderboard_dlg is not None:
            self._leaderboard_dlg.close()
            self._leaderboard_dlg = None

        self.controller.reset_game(difficulty_name=self._difficulty, total_rounds=self._total_rounds, start_loop=True)

    # ---------- Rendering ----------

    def render_snapshot(self, snap) -> None:
        self._last_snapshot = snap

        st = getattr(snap, "game_state", None)
        # Robust string check: handle Enum or string ("NOT_STARTED", "notstarted", etc.)
        st_val = str(getattr(st, "value", st)).upper().replace("_", "")  # "NOTSTARTED"

        cur = int(getattr(snap, "current_round", 0))
        tot = int(getattr(snap, "total_rounds", 0))
        tleft = int(getattr(snap, "time_remaining", 0))

        # Start button Logic: Show ONLY if strictly NOTSTARTED
        is_not_started = (st_val == "NOTSTARTED")
        
        # Intermission Logic
        is_intermission = (st_val == "ROUNDENDING")

        # Update Buttons
        self.btn_start.setVisible(is_not_started)
        self.btn_start.setEnabled(is_not_started)

        # Reset/Exit shown when game is active (i.e. NOT not_started)
        self.btn_reset.setVisible(not is_not_started)
        self.btn_exit.setVisible(not is_not_started)

        # Update Header Label
        if is_not_started:
            state_name = "READY"
        elif is_intermission:
            state_name = "INTERMISSION"
        elif st_val == "GAMECOMPLETE":
            state_name = "GAME OVER"
        elif st_val == "PAUSED":
            state_name = "PAUSED"
        else:
            state_name = "ACTIVE"

        is_game_over = (st_val=="GAMECOMPLETE")
        if is_game_over and not self._game_over_shown:
            settlement = getattr(snap,"settlement_price",None)
            lb = getattr(snap,"leaderboard",None) or []
            if settlement is not None and lb:
                self._game_over_shown = True

                bot_pos = getattr(snap,"bot_positions",{}) or {}
                user_pos = int(getattr(snap,"user_position",0))

                rows = []
                for name, pnl in lb:
                    if str(name) == "YOU":
                        pos = user_pos
                    else:
                        pos = int(bot_pos.get(str(name),0))
                    rows.append((str(name),float(pnl),pos))

                dlg  = GameOverDialog(settlement=int(settlement),parent=self)
                dlg.update_leaderboard(rows)

                dlg.btn_new.clicked.connect(lambda:(dlg.accept(),self._new_game()))
                dlg.btn_exit.clicked.connect(lambda:(dlg.accept(),self.close()))
                
                if self._leaderboard_dlg is not None:
                    self._leaderboard_dlg.close()
                    self._leaderboard_dlg = None
                self.controller.stop_loop()
                try:
                    dlg.exec()
                finally:
                    self.controller.start_loop()

        self.round_label.setText(f"[{state_name}] Round {cur}/{tot}")
        self.timer_label.setText(f"{tleft}s")

        # Digits
        md = getattr(snap, "masked_digits", None)
        if md is None:
            md = getattr(snap, "digits", [])
        self.digits_panel.update_digits(md)

        # Stats
        pnl = float(getattr(snap, "user_pnl", 0.0))
        pos = int(getattr(snap, "user_position", 0))
        cash = float(getattr(snap, "user_cash", 0.0))
        fees = float(getattr(snap, "user_fees", 0.0))
        self.pos_panel.update_stats(pnl, pos, cash, fees)

        # Book
        bids = getattr(snap, "bids", [])
        asks = getattr(snap, "asks", [])
        spread = getattr(snap, "spread", None)
        self.book_widget.update_book(bids, asks, spread)

        # Logs
        alerts = getattr(snap, "recent_alerts", [])
        if alerts:
            self.log_list.clear()
            self.log_list.addItems(alerts[-12:])

        # Trades
        trades = getattr(snap, "recent_trades", [])
        my_trades = [t for t in (trades or []) if "YOU" in str(t)]
        self.trades_list.clear()
        self.trades_list.addItems([str(x) for x in my_trades[-12:]])

        # Leaderboard
        self._maybe_update_leaderboard(snap, is_intermission, cur, tleft)

    def _maybe_update_leaderboard(self, snap, is_intermission: bool, cur_round: int, seconds_left: int) -> None:
        if not is_intermission:
            if self._leaderboard_dlg is not None and self._leaderboard_dlg.isVisible():
                self._leaderboard_dlg.close()
            self._leaderboard_dlg = None
            return

        # Show dialog only once per round
        if self._leaderboard_round_shown != cur_round:
            self._leaderboard_round_shown = cur_round
            self._leaderboard_dlg = LeaderboardDialog(parent=self)
            self._leaderboard_dlg.show()

        if self._leaderboard_dlg is None:
            return

        user_pnl = float(getattr(snap, "user_pnl", 0.0))
        user_pos = int(getattr(snap, "user_position", 0))

        bot_pnls = getattr(snap, "bot_pnls", {}) or {}
        bot_pos = getattr(snap, "bot_positions", {}) or {}

        rows = [("YOU", user_pnl, user_pos)]
        for name, pnl in bot_pnls.items():
            rows.append((str(name), float(pnl), int(bot_pos.get(name, 0))))

        rows.sort(key=lambda x: x[1], reverse=True)

        self._leaderboard_dlg.update_header(round_no=cur_round, seconds_left=seconds_left)
        self._leaderboard_dlg.update_leaderboard(rows)

    def _on_error(self, msg: str) -> None:
        print(msg)
        try:
            self.log_list.addItem(f"[ERR] {msg}")
        except Exception:
            pass

    def closeEvent(self, event):
        self.controller.stop_loop()
        self._settings.setValue("ui/geometry", self.saveGeometry())
        super().closeEvent(event)
        event.accept()
