"""Allowlisted JSON logging; no raw worker exceptions or repository content."""

import json
import logging

from .models import AuditEvent

logger = logging.getLogger("portfolio_agent")


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False


def log_event(event: AuditEvent) -> None:
    logger.info(json.dumps(event.model_dump(mode="json"), sort_keys=True))
