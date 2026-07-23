"""
A no-broker-needed fallback for running the ticket email in the
background, for deployments that don't have Celery + Redis available
(e.g. hosts where a persistent worker + broker isn't free).

This is deliberately NOT a replacement for Celery in general - it's a
fallback used only when Celery genuinely isn't reachable. It satisfies
the same two requirements that matter to the calling code:
  1. The HTTP request that triggers it returns immediately, never
     waiting on PDF generation or email delivery.
  2. A failed attempt is automatically retried with backoff, up to a
     fixed number of attempts, without any caller involvement.

What it does NOT provide, unlike Celery + Redis: durability. If the web
process restarts or crashes mid-retry-wait, a queued email is lost -
there's no persistent queue backing it, just an in-memory thread. For a
low-traffic project this is a reasonable trade-off for not needing any
paid or card-requiring service at all; it would not be an appropriate
substitute for Celery in a higher-stakes production system.
"""

import logging
import threading
import time
import random

logger = logging.getLogger(__name__)

MAX_RETRIES = 4
BASE_DELAY_SECONDS = 5        # first retry after ~5s - kept short since this
MAX_DELAY_SECONDS = 60        # thread needs to finish before a free host's
                               # process might get frozen/killed between requests


def _run_with_retry(fn, args, description):
    """Runs fn(*args) with the same retry/backoff shape as the Celery
    task (autoretry_for + retry_backoff + retry_backoff_max + jitter),
    just implemented as a plain loop instead of relying on a broker."""
    attempt = 0
    while True:
        attempt += 1
        try:
            result = fn(*args)
            logger.info("%s succeeded on attempt %d: %s", description, attempt, result)
            return
        except Exception:
            if attempt > MAX_RETRIES:
                logger.exception(
                    "%s failed permanently after %d attempts.", description, attempt - 1
                )
                return

            delay = min(BASE_DELAY_SECONDS * (2 ** (attempt - 1)), MAX_DELAY_SECONDS)
            delay = delay * (0.85 + random.random() * 0.3)  # jitter, same intent as Celery's retry_jitter
            logger.warning(
                "%s failed on attempt %d, retrying in %.0fs...",
                description, attempt, delay,
                exc_info=True,
            )
            time.sleep(delay)


def send_ticket_email_in_background(order_id):
    """
    Fire-and-forget: spawns a daemon thread that generates and emails the
    ticket, with retries, and returns immediately. Called from
    movies/views.py as the fallback when Celery's .delay() isn't usable.
    """
    from .tasks import _do_send_ticket_email

    thread = threading.Thread(
        target=_run_with_retry,
        args=(_do_send_ticket_email, (order_id,), f"Ticket email for order {order_id}"),
        daemon=True,
    )
    thread.start()
