from __future__ import annotations

import logging
from pathlib import Path

from app.config import get_settings, load_app_config


def setup_logging() -> None:
    settings = get_settings()
    config = load_app_config()
    log_dir = Path(config.global_.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "app.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
