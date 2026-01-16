from __future__ import annotations

from dataclasses import dataclass, asdict
from threading import RLock
from typing import Dict, Optional, Tuple, Any
import time
import uuid

# Injected dependency
from application.market_simulator import MarketSimulator
from infrastructure.persistence import atomic_write_json, read_json

@dataclass(frozen=True)
class SessionMeta:
    session_id: str
    created_at: float
    seed: int
    difficulty_name: str
    user_id: str = "local"


class SessionManager:
    """
    Owns simulator instances keyed by session_id.
    - Thread-safe
    - Supports persistence of lightweight metadata + last snapshot
    """

    def __init__(self):
        self._lock = RLock()
        self._sessions: Dict[str, MarketSimulator] = {}
        self._meta: Dict[str, SessionMeta] = {}

    def create_session(self, config, *, seed: Optional[int] = None, user_id: str = "local") -> SessionMeta:
        with self._lock:
            session_id = str(uuid.uuid4())
            
            # If no seed provided, generate one based on time
            if seed is None:
                seed = int(time.time_ns() % (2**31 - 1))

            # CRITICAL: Pass seed to simulator so digits are deterministic
            sim = MarketSimulator(config, seed=seed)

            meta = SessionMeta(
                session_id=session_id,
                created_at=time.time(),
                seed=seed,
                difficulty_name=getattr(config, "name", "custom"),
                user_id=user_id
            )

            self._sessions[session_id] = sim
            self._meta[session_id] = meta

            return meta

    def get(self, session_id: str) -> MarketSimulator:
        with self._lock:
            if session_id not in self._sessions:
                raise KeyError(f"Unknown session_id: {session_id}")
            return self._sessions[session_id]

    def get_meta(self, session_id: str) -> SessionMeta:
        with self._lock:
            if session_id not in self._meta:
                raise KeyError(f"Unknown session_id: {session_id}")
            return self._meta[session_id]

    def close_session(self, session_id: str) -> bool:
        with self._lock:
            existed = session_id in self._sessions
            self._sessions.pop(session_id, None)
            self._meta.pop(session_id, None)
            return existed

    def list_sessions(self) -> Dict[str, SessionMeta]:
        with self._lock:
            return dict(self._meta)

    def save_checkpoint(self, session_id: str, path: str) -> None:
        """
        Saves:
        - session metadata (including seed)
        - a UI snapshot (for resume UX)
        """
        with self._lock:
            sim = self.get(session_id)
            meta = self.get_meta(session_id)
            snap = sim.get_state_snapshot()

            payload = {
                "meta": asdict(meta),
                "snapshot": snap.__dict__,
                "saved_at": time.time(),
            }
            atomic_write_json(path, payload)

    def load_checkpoint(self, path: str, config_factory) -> Tuple[SessionMeta, MarketSimulator]:
        """
        Restores a simulator from checkpoint.
        Note: This is a UX resume (loads snapshot for UI).
        To fully restore state, one would typically replay the event log.
        However, re-initializing with the SAME SEED ensures digits/settlement are identical.
        """
        data: Dict[str, Any] = read_json(path)
        meta_d = data["meta"]

        # Reconstruct config from name
        difficulty_name = meta_d.get("difficulty_name", "custom")
        if hasattr(config_factory, difficulty_name):
            config = getattr(config_factory, difficulty_name)()
        else:
            # Fallback or custom handling
            config = config_factory("MEDIUM") 

        # Create new session with the SAME SEED
        meta = self.create_session(
            config, 
            seed=int(meta_d["seed"]), 
            user_id=meta_d.get("user_id", "local")
        )

        sim = self.get(meta.session_id)
        # Optional: You could inject the loaded snapshot into the sim for UI purposes
        # sim._loaded_snapshot_for_ui = data.get("snapshot")

        return meta, sim
