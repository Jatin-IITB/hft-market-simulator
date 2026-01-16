# cli_play.py

from __future__ import annotations

import threading
import time
from typing import Optional

from infrastructure.config import DifficultyConfig
from infrastructure.logger import configure_logging, LoggingConfig
from application.session_manager import SessionManager
from application.replay_manager import ReplayManager
from application.market_simulator import GameState

TICK_INTERVAL_SEC = 0.20  # market heartbeat


def _fmt(x: Optional[float], fmt: str = "{:.1f}") -> str:
    return "-" if x is None else fmt.format(x)


def _fmt_lb(lb):
    if not lb:
        return "-"
    top = lb[:5]
    return " | ".join([f"{i+1}:{name}({pnl:.2f})" for i, (name, pnl) in enumerate(top)])


def print_snap(s) -> None:
    digits_str = " ".join(s.masked_digits) if s.masked_digits else " ".join(["?"] * s.total_rounds)

    state_display = s.game_state.value
    if s.game_state == GameState.ROUND_ENDING:
        state_display = "INTERMISSION (Next round soon...)"

    print(
        f"\nState={state_display} Round={s.current_round}/{s.total_rounds} T={s.time_remaining}s\n"
        f"Digits: {digits_str}\n"
        f"BB/BA={_fmt(s.best_bid)}/{_fmt(s.best_ask)} Spr={_fmt(s.spread)} Mid={_fmt(s.mid_price)}\n"
        f"YOU: pos={s.user_position} cash={s.user_cash:.2f} fees={s.user_fees:.2f} pnl={s.user_pnl:.2f} tox={s.user_toxicity:.2f}\n"
    )

    if s.game_state == GameState.ROUND_ACTIVE:
        print("Bids:", s.bids)
        print("Asks:", s.asks)

    if s.recent_trades:
        print("Trades:", s.recent_trades[-5:])

    if s.recent_alerts:
        print("Alerts:", s.recent_alerts[-3:])

    if s.game_state in (GameState.ROUND_ENDING, GameState.GAME_COMPLETE) and s.leaderboard:
        print("\n🏆 Leaderboard:", _fmt_lb(s.leaderboard))

    if s.game_state == GameState.GAME_COMPLETE and s.settlement_price is not None:
        print(f"🏁 Settlement: {s.settlement_price}")


class MarketRunner:
    def __init__(self, sim):
        self.sim = sim
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="market-loop")

    def start(self) -> None:
        self.thread.start()

    def shutdown(self, timeout: float = 2.0) -> None:
        self.stop.set()
        self.thread.join(timeout=timeout)

    def _loop(self) -> None:
        while not self.stop.is_set():
            try:
                self.sim.tick()
            except Exception:
                pass
            time.sleep(TICK_INTERVAL_SEC)


def main() -> None:
    configure_logging(LoggingConfig(level="INFO", log_file="runs/sim.log"))

    cfg = DifficultyConfig.MEDIUM()
    sessions = SessionManager()
    meta = sessions.create_session(cfg, user_id="local")
    sim = sessions.get(meta.session_id)

    replay = ReplayManager()
    replay.attach(sim=sim, session_meta=meta)

    runner = MarketRunner(sim)
    runner.start()

    print("Commands:")
    print("  start            -> start Round 1")
    print("  snap             -> print snapshot")
    print("  mm <bid> <ask> [q] -> make market")
    print("  buy <px> [q]     -> limit-buy")
    print("  sell <px> [q]    -> limit-sell")
    print("  lift [q]         -> buy at best ask")
    print("  hit  [q]         -> sell at best bid")
    print("  cancel           -> cancel orders")
    print("  save             -> save checkpoint")
    print("  quit             -> exit\n")
    
    print("Tip: Rounds auto-advance after 10s intermission.")
    print("Type `start` to begin Round 1.\n")
    
    print_snap(sim.get_state_snapshot())

    def ensure_round_active() -> None:
        st = sim.get_state_snapshot()
        if st.game_state == GameState.NOT_STARTED:
             sim.start_round(1)

    while True:
        try:
            line = input("axxela> ").strip()
        except (EOFError, KeyboardInterrupt):
            line = "quit"

        if not line:
            continue

        parts = line.split()
        cmd = parts[0].lower()

        try:
            if cmd == "quit":
                break

            if cmd == "start":
                st = sim.get_state_snapshot()
                if st.game_state == GameState.NOT_STARTED:
                    replay.record_command("start_round", {"round": 1})
                    sim.start_round(1)
                else:
                    print("Game already started! Use `snap` to see status.")
                print_snap(sim.get_state_snapshot())
                continue

            if cmd == "snap":
                print_snap(sim.get_state_snapshot())
                continue

            if cmd in ("mm", "buy", "sell", "lift", "hit", "cancel"):
                ensure_round_active()
                
                if cmd == "mm":
                    bid = float(parts[1])
                    ask = float(parts[2])
                    qty = int(parts[3]) if len(parts) > 3 else 1
                    replay.record_command("make_market", {"bid": bid, "ask": ask, "qty": qty})
                    ok = sim.make_market(bid, ask, qty)
                    if not ok: print("Rejected.")
                    print_snap(sim.get_state_snapshot())
                    continue

                if cmd == "buy":
                    px = float(parts[1])
                    qty = int(parts[2]) if len(parts) > 2 else 1
                    replay.record_command("aggress_buy", {"price": px, "qty": qty})
                    ok = sim.aggress_buy(px, qty)
                    if not ok: print("Rejected.")
                    print_snap(sim.get_state_snapshot())
                    continue

                if cmd == "sell":
                    px = float(parts[1])
                    qty = int(parts[2]) if len(parts) > 2 else 1
                    replay.record_command("aggress_sell", {"price": px, "qty": qty})
                    ok = sim.aggress_sell(px, qty)
                    if not ok: print("Rejected.")
                    print_snap(sim.get_state_snapshot())
                    continue

                if cmd == "lift":
                    qty = int(parts[1]) if len(parts) > 1 else 1
                    snap = sim.get_state_snapshot()
                    if snap.best_ask is None:
                        print("No asks to lift.")
                    else:
                        px = float(snap.best_ask)
                        replay.record_command("lift", {"price": px, "qty": qty})
                        ok = sim.aggress_buy(px, qty)
                        if not ok: print("Rejected.")
                    print_snap(sim.get_state_snapshot())
                    continue

                if cmd == "hit":
                    qty = int(parts[1]) if len(parts) > 1 else 1
                    snap = sim.get_state_snapshot()
                    if snap.best_bid is None:
                        print("No bids to hit.")
                    else:
                        px = float(snap.best_bid)
                        replay.record_command("hit", {"price": px, "qty": qty})
                        ok = sim.aggress_sell(px, qty)
                        if not ok: print("Rejected.")
                    print_snap(sim.get_state_snapshot())
                    continue

                if cmd == "cancel":
                    replay.record_command("cancel_user_orders", {})
                    sim.cancel_user_orders()
                    print_snap(sim.get_state_snapshot())
                    continue

            if cmd == "save":
                sessions.save_checkpoint(meta.session_id, "runs/last_checkpoint.json")
                replay.save("runs/last_replay.jsonl")
                print("Saved checkpoint.")
                continue

            print("Unknown command.")

        except Exception as e:
            print("Error:", e)
            print_snap(sim.get_state_snapshot())

    runner.shutdown()
    sessions.save_checkpoint(meta.session_id, "runs/last_checkpoint.json")
    replay.save("runs/last_replay.jsonl")
    print("Bye.")


if __name__ == "__main__":
    main()
