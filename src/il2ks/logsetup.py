"""Per-process logging: rotating file in the data directory plus stderr (TD-27).

STAND-IN on the ingest-jobs branch: the catalog agent owns the real implementation. Same signature.
"""

import logging
import logging.handlers
from pathlib import Path


def configure_logging(process: str, log_dir: Path, level: str = "INFO") -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(f"%(asctime)s %(levelname)s {process} %(name)s: %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / f"{process}.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    stream_handler = logging.StreamHandler()
    root = logging.getLogger()
    for handler in (file_handler, stream_handler):
        handler.setFormatter(fmt)
        root.addHandler(handler)
    root.setLevel(level)
