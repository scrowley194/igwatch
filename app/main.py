# app/main.py
# Orchestrator: run enabled watchers → fetch & parse → format email → send → persist state

from __future__ import annotations
import os
import logging
from pathlib import Path
from typing import Iterable, List, Tuple

from .utils.log import get_logger
from .utils.state import State
from .emailers import smtp_oauth
from .emailers.templates import render_subject, render_body
from .parsers.extract import fetch_and_summarize
from .watchers.sec_edgar import SecEdgarWatcher
from .watchers.rns_lse import RnsLseWatcher
from .watchers.ir_sources import IrSourcesWatcher

logger = get_logger("igwatch")
DIV = "-" * 72


def _truthy(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def _get_env_list(name: str) -> List[str]:
    raw = os.getenv(name, "")
    return [x.strip() for x in raw.split(",") if x.strip()]


def _send_email(subject: str, body: str) -> None:
    if _truthy("DRY_RUN", False):
        logger.info("[DRY RUN] Would send email: %s\n%s", subject, body)
        return
    mail_to = _get_env_list("MAIL_TO")
    mail_from = os.getenv("MAIL_FROM")
    if not mail_to:
        logger.warning("MAIL_TO not set; skipping email send. Subject=%s", subject)
        return
    smtp_oauth.send_plaintext(subject, body, mail_to, mail_from=mail_from)


def _iter_items_from_watcher(w) -> Iterable[Tuple[str, str]]:
    try:
        items = w.poll()
    except Exception:
        logger.exception("Watcher %s.poll() raised", w.__class__.__name__)
        return []
    if not items:
        logger.info("Watcher %s returned no items.", w.__class__.__name__)
        return []

    safe: List[Tuple[str, str]] = []
    for idx, it in enumerate(items):
        try:
            url, title = it
            if not url:
                raise ValueError("empty url")
            safe.append((url, title or ""))
        except Exception as e:
            logger.warning(
                "Watcher %s item[%d] malformed (%s); skipping. Item=%r",
                w.__class__.__name__, idx, e, it,
            )
    return safe


def process_item(state: State, url: str, title: str) -> None:
    if state.has(url):
        logger.debug("Already processed: %s", url)
        return

    payload = None
    try:
        payload = fetch_and_summarize(url, title_hint=title)
    except Exception:
        logger.exception("fetch_and_summarize failed for %s", url)
        return

    if not payload:
        logger.warning("No payload for %s", url)
        return

    subject = render_subject(payload)
    body = render_body(payload)

    try:
        _send_email(subject, body)
    except Exception:
        logger.exception("Email send failed for %s", url)
        return

    state.add(url)
    logger.info("Sent email for %s", url)


def main() -> None:
    state_path = Path(os.getenv("STATE_FILE", "data/seen.json"))
    legacy = Path("data/seen.db")
    if legacy.exists() and not state_path.exists():
        try:
            bak = legacy.with_suffix(legacy.suffix + ".legacy")
            legacy.replace(bak)
            logger.warning("Found legacy state at %s; moved to %s.", legacy, bak)
        except Exception:
            logger.exception("Could not move legacy state %s; proceeding.", legacy)

    state = State(state_path)
    start_days = int(os.getenv("START_FROM_DAYS", "90"))
    watchers: List[object] = []

    if _truthy("ENABLE_EDGAR", True):
        watchers.append(SecEdgarWatcher(start_days=start_days))
    if _truthy("ENABLE_LSE", True):
        watchers.append(RnsLseWatcher(start_days=start_days))
    if _truthy("ENABLE_IR", True):
        watchers.append(IrSourcesWatcher(start_days=start_days))

    if not watchers:
        logger.warning("No watchers enabled. Set ENABLE_EDGAR/LSE/IR=true to enable sources.")
        return

    seen_this_run: set[str] = set()
    for w in watchers:
        logger.info(DIV)
        logger.info("Checking %s", w.__class__.__name__)
        for url, title in _iter_items_from_watcher(w):
            if url in seen_this_run:
                continue
            seen_this_run.add(url)
            try:
                process_item(state, url, title)
            except Exception:
                logger.exception("process_item failed for %s", url)

    try:
        state.save()
    except Exception:
        logger.exception("Failed to persist state")


if __name__ == "__main__":
    main()
