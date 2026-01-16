# main.py
from __future__ import annotations

import time
from infrastructure.config import DifficultyConfig
from infrastructure.logger import configure_logging, LoggingConfig
from application.session_manager import SessionManager
from application.replay_manager import ReplayManager

def main() -> None:
    configure_logging(LoggingConfig(level="INFO", log_file="runs/sim.log"))

    cfg = DifficultyConfig.MEDIUM()
    sessions = SessionManager()
    meta = sessions.create_session(cfg, user_id="local")

    sim = sessions.get(meta.session_id)
    replay = ReplayManager()
    replay.attach(sim=sim, session_meta=meta)

    sim.start_round(1)

    t0 = time.time()
    while time.time() - t0 < 3.0:
        sim.tick()
        time.sleep(0.15)

    # Optional: record one snapshot for visual replay
    snap = sim.get_state_snapshot()
    replay.record_snapshot(snap)

    # Save artifacts
    sessions.save_checkpoint(meta.session_id, "runs/last_checkpoint.json")
    replay.save("runs/last_replay.jsonl")

    print("OK:", snap.game_state, "FV=", snap.fair_value, "Pos=", snap.user_position, "PnL=", snap.user_pnl)

if __name__ == "__main__":
    main()
