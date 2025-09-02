# nbm_core/log.py
"""
Initializes and configures the application's logger.
"""

import logging


def setup_logging() -> logging.Logger:
    """
    Configures and returns the main logger for the application.

    This setup ensures that logs are written to 'manage.log' for debugging
    and printed to the console for user feedback. It avoids duplicate
    handlers if called multiple times.
    """
    log_file = "manage.log"
    logger = logging.getLogger("PluginManager")
    logger.setLevel(logging.DEBUG)

    # Avoid adding handlers multiple times if this function is called again
    if logger.hasHandlers():
        return logger

    # File handler for detailed debug logs
    fh = logging.FileHandler(log_file, encoding="utf-8", mode="a")
    fh.setLevel(logging.DEBUG)
    formatter_file = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(threadName)s - %(message)s"
    )
    fh.setFormatter(formatter_file)
    logger.addHandler(fh)

    # Console handler for user-facing info
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    formatter_console = logging.Formatter("%(message)s")
    ch.setFormatter(formatter_console)
    logger.addHandler(ch)

    return logger
