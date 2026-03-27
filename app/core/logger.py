import os
import sys
import time
import logging
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler


class SizeAndTimeRotatingFileHandler(TimedRotatingFileHandler):
    def __init__(self, filename, when="midnight", interval=1, backupCount=7, encoding=None, delay=False, utc=False, atTime=None, maxBytes=0):
        self.maxBytes = int(maxBytes) if maxBytes else 0
        super().__init__(filename, when=when, interval=interval, backupCount=backupCount,
                         encoding=encoding, delay=delay, utc=utc, atTime=atTime)

    def shouldRollover(self, record):
        if self.stream is None:
            self.stream = self._open()
        if int(time.time()) >= self.rolloverAt:
            return 1
        if self.maxBytes > 0:
            msg = f"{self.format(record)}\n"
            self.stream.seek(0, os.SEEK_END)
            if self.stream.tell() + len(msg.encode(self.encoding or "utf-8")) >= self.maxBytes:
                return 1
        return 0


def _script_name():
    main_mod = sys.modules.get("__main__")
    path = getattr(main_mod, "__file__", None) or (
        sys.argv[0] if sys.argv else "")
    name = os.path.basename(path) if path else "app"
    base, _ = os.path.splitext(name)
    return base or "app"


def _default_log_base_dir():
    app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_root = os.path.dirname(app_root)
    return os.path.join(project_root, "logs")


def setup_logging(cfg):
    try:
        level_name = str(cfg.get("log_level", "INFO")).upper()
        level = getattr(logging, level_name, logging.INFO)
        base_dir = str(cfg.get("log_base_dir") or cfg.get(
            "log_dir") or _default_log_base_dir())
        base_dir = os.path.abspath(base_dir)
        date_dir = datetime.now().strftime("%Y-%m-%d")
        log_dir = os.path.join(base_dir, date_dir)
        os.makedirs(log_dir, exist_ok=True)
        test_file = os.path.join(log_dir, ".write_check")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("")
        os.remove(test_file)

        logger = logging.getLogger("law_assistant")
        logger.setLevel(level)
        logger.handlers = []
        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(threadName)s | %(module)s | %(message)s")
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        logger.addHandler(stream)

        script = _script_name()
        log_path = os.path.join(log_dir, f"{script}.log")
        handler = SizeAndTimeRotatingFileHandler(
            log_path,
            when=str(cfg.get("log_when", "midnight")),
            interval=int(cfg.get("log_interval", 1)),
            backupCount=int(cfg.get("log_backup_count", 7)),
            encoding="utf-8",
            maxBytes=int(cfg.get("log_max_bytes", 10 * 1024 * 1024)),
        )
        handler.setFormatter(fmt)
        logger.addHandler(handler)
        logger.propagate = False
        logger.info("logging_ready level=%s log_path=%s", level_name, log_path)
        return logger
    except Exception as e:
        fallback_dir = _default_log_base_dir()
        try:
            os.makedirs(fallback_dir, exist_ok=True)
        except Exception:
            pass
        logger = logging.getLogger("law_assistant")
        logger.setLevel(logging.INFO)
        logger.handlers = []
        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(threadName)s | %(module)s | %(message)s")
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        logger.addHandler(stream)
        try:
            fallback_path = os.path.join(fallback_dir, "fallback.log")
            fh = logging.FileHandler(fallback_path, encoding="utf-8")
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except Exception:
            pass
        logger.error("Logging initialization failed: %s", str(e))
        return logger
