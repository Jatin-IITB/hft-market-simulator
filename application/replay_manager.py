from __future__ import annotations

from dataclasses import dataclass, asdict
from threading import RLock
from typing import Any, Dict, List, Optional, Callable
import time

from infrastructure.persistence import atomic_write_jsonl, read_jsonl


@dataclass(frozen=True)
class ReplayHeader:
    version: int
    created_at: float
    session_id: str
    seed: int
    difficulty_name: str


class ReplayManager:
    """
    Records an event stream + user commands to JSONL (append-friendly).

    This is “audit-grade logging”. Exact determinism requires:
    - simulator to use injected RNG (random.Random(seed))
    - simulator to use injected time source (not time.time())
    If you want that, say so and the simulator can be patched cleanly.
    """

    def __init__(self):
        self._lock = RLock()
        self._header: Optional[ReplayHeader] = None
        self._records: List[Dict[str, Any]] = []
        self._unsubscribe: Optional[Callable[[], None]] = None

    def attach(self, *, sim, session_meta) -> None:
        """
        Attach to a MarketSimulator instance and record its MarketEvents.
        """
        with self._lock:
            self._header = ReplayHeader(
                version=1,
                created_at=time.time(),
                session_id=session_meta.session_id,
                seed=session_meta.seed,
                difficulty_name=session_meta.difficulty_name,
            )
            def on_event(ev):
                self._append_record({"type": "event", "ts": ev.timestamp, "event": ev.__dict__})
            self._unsubscribe=sim.subscribe_to_events(on_event)
            
            # best-effort: record periodic snapshots if you want (off by default)
            self._append_record({"type": "header", "ts": time.time(), "header": asdict(self._header)})

    def detach(self) -> None:
        with self._lock:
            if self._unsubscribe:
                self._unsubscribe()
                self._unsubscribe = None

    def record_command(self, command: str, payload: Dict[str, Any]) -> None:
        """
        Call this from your UI/controller whenever the user issues a command.
        Example:
            replay.record_command("make_market", {"bid": 27.0, "ask": 29.0, "qty": 1})
        """
        self._append_record({"type": "command", "ts": time.time(), "command": command, "payload": payload})

    def record_snapshot(self, snapshot_obj) -> None:
        """
        Optional: snapshots make replay “visual” without requiring deterministic re-sim.
        """
        self._append_record({"type": "snapshot", "ts": time.time(), "snapshot": snapshot_obj.__dict__})

    def _append_record(self, rec: Dict[str, Any]) -> None:
        with self._lock:
            self._records.append(rec)
            if len(self._records)>=10_000:
                atomic_write_jsonl("runs/autosave_replay.jsonl", self._records)

    def save(self, path_jsonl: str) -> None:
        with self._lock:
            atomic_write_jsonl(path_jsonl, self._records)

    @staticmethod
    def load(path_jsonl: str) -> List[Dict[str, Any]]:
        return read_jsonl(path_jsonl)


class ReplayPlayer:
    """
    Simple playback that yields snapshots/events in timestamp order.
    (Does not re-run the simulator; it replays the recorded stream.)
    """

    def __init__(self, records: List[Dict[str, Any]]):
        self.records = sorted(records, key=lambda r: (r.get("ts", 0.0), r.get("type", "")))

    def iter_events(self):
        for r in self.records:
            if r.get("type") == "event":
                yield r["event"]

    def iter_snapshots(self):
        for r in self.records:
            if r.get("type") == "snapshot":
                yield r["snapshot"]

    def iter_commands(self):
        for r in self.records:
            if r.get("type") == "command":
                yield (r["command"], r["payload"], r.get("ts", 0.0))
