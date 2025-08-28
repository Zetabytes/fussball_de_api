import logging

from .config import settings


def setup_logging():
    """
    Configures the application's logging.

    This function sets up a unified logging format for the application
    and for Uvicorn. It configures the root logger with a standard
    stream handler and then removes Uvicorn's default handlers to ensure
    all logs are processed by the root logger, resulting in a consistent format.
    """
    log_level = settings.LOG_LEVEL.upper()

    # Configure a standard StreamHandler
    handler = logging.StreamHandler()

    # Configure formatter according to conventions
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-25.25s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Clear any existing handlers from root logger and add our new one
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
    root_logger.addHandler(handler)

    # Remove Uvicorn's default handlers and ensure propagation to root
    for name in ["uvicorn", "uvicorn.error", "uvicorn.access"]:
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True

    # Set httpx log level to WARNING to avoid verbose output from the library
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Log the initial configuration message using a specific app logger
    logging.getLogger("fussball_api.config").info(f"Logging configured with level: {log_level}")
