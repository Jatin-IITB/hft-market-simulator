# infrastructure/persistence.py

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Dict, Iterable, List

def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)

def _atomic_write_bytes(path: str, data: bytes) -> None:
    """
    Write to a temp file in the same directory and replace.
    This is the standard pattern for atomic-ish replacement with os.replace.
    """
    _ensure_parent_dir(path)
    target_dir = os.path.dirname(os.path.abspath(path)) or "."
    
    fd = None
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", dir=target_dir)
        with os.fdopen(fd, "wb") as f:
            fd = None
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

def to_jsonable(x: Any) -> Any:
    """
    Convert common Python objects used in this project into JSON-serializable
    primitives (dict/list/str/int/float/bool/None).
    """
    if x is None or isinstance(x, (str, int, float, bool)):
        return x
    if isinstance(x, Enum):
        return x.value
    if is_dataclass(x):
        return to_jsonable(asdict(x))
    if isinstance(x, dict):
        return {str(k): to_jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [to_jsonable(v) for v in x]
    return str(x)

def atomic_write_json(path: str, obj: Dict[str, Any]) -> None:
    safe_obj = to_jsonable(obj)
    payload = json.dumps(safe_obj, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    _atomic_write_bytes(path, payload)

def read_json(path: str) -> Dict[str, Any]:
    with open(path, "rb") as f:
        return json.loads(f.read().decode("utf-8"))

def atomic_write_jsonl(path: str, records: Iterable[Dict[str, Any]]) -> None:
    lines: List[str] = []
    for r in records:
        lines.append(json.dumps(to_jsonable(r), ensure_ascii=False, separators=(",", ":")))
    
    data = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
    _atomic_write_bytes(path, data)

def read_jsonl(path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with open(path, "rb") as f:
        for raw in f:
            s = raw.decode("utf-8").strip()
            if not s:
                continue
            out.append(json.loads(s))
    return out
