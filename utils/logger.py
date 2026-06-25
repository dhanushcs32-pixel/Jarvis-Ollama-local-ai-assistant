"""Shared logging configuration."""

import logging
import sys


def setup_logger(name: str = "assistant") -> logging.Logger:
    logging.basicConfig(
        stream=sys.stdout,
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger(name)
