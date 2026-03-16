import os
import logging
from logging.handlers import RotatingFileHandler


def setup_logging(cfg):
    level_name = str(cfg.get("log_level", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    log_dir = cfg.get("log_dir") or os.path.join(
        cfg.get("data_dir", "data"), "logs")
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger("law_assistant")
    logger.setLevel(level)
    logger.handlers = []
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    logger.addHandler(stream)
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "app.log"),
        maxBytes=int(cfg.get("log_max_bytes", 5 * 1024 * 1024)),
        backupCount=int(cfg.get("log_backup_count", 3)),
        encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    logger.propagate = False
    return logger
