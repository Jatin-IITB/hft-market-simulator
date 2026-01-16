# infrastructure/logger.py
from __future__ import annotations

import logging
import logging.config
from dataclasses import dataclass
from typing import Optional, Dict, Any
from pathlib import Path

@dataclass(frozen=True)
class LoggingConfig:
    app_name: str = "clob_sim"
    level: str = "INFO"
    log_file: Optional[str] = None
    propagate_root: bool = False


def build_dict_config(cfg: LoggingConfig) -> Dict[str, Any]:
    handlers: Dict[str, Any] = {
        "console": {
            "class": "logging.StreamHandler",
            "level": cfg.level,
            "formatter": "standard",
            "stream": "ext://sys.stdout",
        }
    }

    root_handlers = ["console"]

    if cfg.log_file:
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "level": cfg.level,
            "formatter": "standard",
            "filename": cfg.log_file,
            "maxBytes": 5_000_000,
            "backupCount": 3,
            "encoding": "utf-8",
        }
        root_handlers.append("file")

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            }
        },
        "handlers": handlers,
        "root": {
            "level": cfg.level,
            "handlers": root_handlers,
        },
        "loggers": {
            # Keep noise down from third-party libs if needed:
            "urllib3": {"level": "WARNING"},
        },
    }


def configure_logging(cfg: LoggingConfig) -> None:
    """
    Configure logging once from your entrypoint (main.py / app start).
    Uses dictConfig which is the supported configuration mechanism in stdlib. [web:88]
    """
    if cfg.log_file:
        Path(cfg.log_file).parent.mkdir(parents=True, exist_ok=True)
    logging.config.dictConfig(build_dict_config(cfg))


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
