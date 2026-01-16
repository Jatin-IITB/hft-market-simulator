from __future__ import annotations

from dataclasses import is_dataclass, replace
from typing import Optional, Any

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot

from application.session_manager import SessionManager
from infrastructure.config import DifficultyConfig


class MarketController(QObject):
    """
    Single owner of the simulator for the GUI.

    - Owns SessionManager + MarketSimulator.
    - Runs periodic sim.tick() via QTimer (UI thread).
    - Emits MarketSnapshot objects to the UI (immutable snapshot boundary).
    """

    snapshot_updated = pyqtSignal(object)  # MarketSnapshot
    error_raised = pyqtSignal(str)

    def __init__(self, difficulty_name: str = "MEDIUM", total_rounds: Optional[int] = None):
        super().__init__()

        self._timer = QTimer(self)
        self._timer.setInterval(200)  # 5Hz UI heartbeat
        self._timer.timeout.connect(self._on_tick)

        self._in_tick = False

        self._difficulty_name = difficulty_name
        self._total_rounds = total_rounds

        self._session_manager: Optional[SessionManager] = None
        self._meta = None
        self.sim = None

        self.reset_game(difficulty_name=difficulty_name, total_rounds=total_rounds, start_loop=False)

    # ---------- Lifecycle ----------

    def start_loop(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop_loop(self) -> None:
        if self._timer.isActive():
            self._timer.stop()

    def emit_snapshot(self) -> None:
        try:
            self.snapshot_updated.emit(self._get_snapshot())
        except Exception as e:
            self.error_raised.emit(str(e))

    # ---------- Tick loop ----------

    def _on_tick(self) -> None:
        if self.sim is None:
            return

        self._in_tick = True
        try:
            self._call("tick")
            self.snapshot_updated.emit(self._get_snapshot())
        except Exception as e:
            self.error_raised.emit(f"Tick loop error: {e}")
        finally:
            self._in_tick = False

    # ---------- UI commands (write-only) ----------

    @pyqtSlot()
    def start_game(self) -> None:
        """Starts Round 1 once. Subsequent rounds auto-advance in engine."""
        try:
            if self.sim is None:
                return

            snap = self._get_snapshot()
            st = getattr(snap, "gamestate", getattr(snap, "game_state", None))
            st_val = getattr(st, "value", st)

            if str(st_val) in ("notstarted", "NOT_STARTED", "not_started"):
                self._call("start_round", 1, alt="startround")
        except Exception as e:
            self.error_raised.emit(f"Start game failed: {e}")

    @pyqtSlot(float, float, int)
    def make_market(self, bid: float, ask: float, qty: int) -> None:
        self._call("make_market", bid, ask, qty, alt="makemarket")

    @pyqtSlot(float, int)
    def buy_limit(self, price: float, qty: int) -> None:
        self._call("aggress_buy", price, qty, alt="aggressbuy")

    @pyqtSlot(float, int)
    def sell_limit(self, price: float, qty: int) -> None:
        self._call("aggress_sell", price, qty, alt="aggresssell")

    @pyqtSlot(int)
    def lift_ask(self, qty: int) -> None:
        try:
            snap = self._get_snapshot()
            best_ask = getattr(snap, "bestask", getattr(snap, "best_ask", None))
            if best_ask is None:
                return
            self.buy_limit(float(best_ask), int(qty))
        except Exception as e:
            self.error_raised.emit(f"Lift failed: {e}")

    @pyqtSlot(int)
    def hit_bid(self, qty: int) -> None:
        try:
            snap = self._get_snapshot()
            best_bid = getattr(snap, "bestbid", getattr(snap, "best_bid", None))
            if best_bid is None:
                return
            self.sell_limit(float(best_bid), int(qty))
        except Exception as e:
            self.error_raised.emit(f"Hit failed: {e}")

    @pyqtSlot()
    def cancel_all(self) -> None:
        self._call("cancel_user_orders", alt="canceluserorders")

    # ---------- New Game / reset ----------

    def reset_game(
        self,
        *,
        difficulty_name: str = "MEDIUM",
        total_rounds: Optional[int] = None,
        start_loop: bool = True,
    ) -> None:
        if self._in_tick:
            QTimer.singleShot(
                0,
                lambda: self.reset_game(
                    difficulty_name=difficulty_name,
                    total_rounds=total_rounds,
                    start_loop=start_loop,
                ),
            )
            return

        was_running = self._timer.isActive()
        self.stop_loop()

        try:
            cfg = self._build_config(difficulty_name, total_rounds)
            self._difficulty_name = difficulty_name
            self._total_rounds = total_rounds

            self._session_manager = SessionManager()
            self._meta = self._session_manager.create_session(cfg, user_id="gui_user")
            self.sim = self._session_manager.get(self._meta.session_id)

            self.emit_snapshot()
        except Exception as e:
            self.error_raised.emit(f"Reset game failed: {e}")
        finally:
            if start_loop and (was_running or True):
                self.start_loop()

    # ---------- Internal helpers ----------

    def _get_snapshot(self):
        if self.sim is None:
            raise RuntimeError("Simulator not initialized")
        if hasattr(self.sim, "get_state_snapshot"):
            return self.sim.get_state_snapshot()
        if hasattr(self.sim, "getstatesnapshot"):
            return self.sim.getstatesnapshot()
        raise AttributeError("Simulator missing get_state_snapshot/getstatesnapshot")

    def _call(self, method: str, *args, alt: Optional[str] = None):
        if self.sim is None:
            raise RuntimeError("Simulator not initialized")
        if hasattr(self.sim, method):
            return getattr(self.sim, method)(*args)
        if alt and hasattr(self.sim, alt):
            return getattr(self.sim, alt)(*args)
        raise AttributeError(f"Simulator missing method: {method}" + (f" (alt={alt})" if alt else ""))

    def _build_config(self, difficulty_name: str, total_rounds: Optional[int]):
        dn = (difficulty_name or "MEDIUM").upper()
        if dn == "EASY":
            cfg = DifficultyConfig.EASY()
        elif dn == "HARD":
            cfg = DifficultyConfig.HARD()
        elif dn == "AXXELA":
            cfg = DifficultyConfig.AXXELA()
        else:
            cfg = DifficultyConfig.MEDIUM()

        if total_rounds is None:
            return cfg

        tr = int(total_rounds)
        if tr < 1:
            raise ValueError("total_rounds must be >= 1")

        if hasattr(cfg, "with_rounds"):
            return cfg.with_rounds(tr)

        if is_dataclass(cfg):
            if hasattr(cfg, "total_rounds"):
                return replace(cfg, total_rounds=tr)
            if hasattr(cfg, "totalrounds"):
                return replace(cfg, totalrounds=tr)

        if hasattr(cfg, "total_rounds"):
            setattr(cfg, "total_rounds", tr)
            return cfg

        raise ValueError("Config does not support total_rounds override")
