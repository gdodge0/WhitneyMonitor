from __future__ import annotations

import datetime as _dt
import logging
import os
import sys
from typing import Optional

from celery import Task, shared_task
from celery.signals import worker_process_init, worker_process_shutdown

from .browser import Browser

# ---------------------------------------------------------------------------#
# Constants
# ---------------------------------------------------------------------------#
QUEUE_NAME = "Inyo.Harvester"  # dedicated Celery queue for all harvesting tasks

# ---------------------------------------------------------------------------#
# Logging
# ---------------------------------------------------------------------------#
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------------#
# Tunables
# ---------------------------------------------------------------------------#
# Set either of these to ``None`` to disable the corresponding limit completely.
_MAX_USES: Optional[int] = None  # max calls to a Browser instance before recycling
_MAX_AGE: Optional[_dt.timedelta] = None  # _dt.timedelta(days=3)  # max age of a Browser


# ---------------------------------------------------------------------------#
# Per-worker singleton
# ---------------------------------------------------------------------------#
class _BrowserHolder:
    """Lives **inside** each worker process; never shared across processes."""

    def __init__(self) -> None:
        self._br: Browser | None = None
        self._created_at: _dt.datetime | None = None
        self._use_count: int = 0

    # Public interface --------------------------------------------------#
    def get(self) -> Browser:
        """Return a ready browser, recycling if needed."""
        if self._needs_recycle():
            self._create()
        self._use_count += 1
        return self._br  # type: ignore[return-value]

    def close(self) -> None:
        if self._br:
            try:
                self._br.close()
            finally:
                self._br = None

    # Internals ---------------------------------------------------------#
    def _create(self) -> None:
        self.close()  # tear down any old instance safely

        logger.info("PID %s – creating new browser", os.getpid())
        br = Browser(headless=False)
        br.prep_v3()

        self._br = br
        self._created_at = _dt.datetime.now(_dt.UTC)
        self._use_count = 0

    def _needs_recycle(self) -> bool:
        if not self._br:
            return True

        # Age constraint ------------------------------------------------#
        if _MAX_AGE is None or self._created_at is None:
            age_ok = True
        else:
            age_ok = (_dt.datetime.now(_dt.UTC) - self._created_at) < _MAX_AGE

        # Use-count constraint -----------------------------------------#
        if _MAX_USES is None:
            uses_ok = True
        else:
            uses_ok = self._use_count < _MAX_USES

        return not (age_ok and uses_ok)


_holder = _BrowserHolder()  # One instance *per* worker process


# ---------------------------------------------------------------------------#
# Helpers
# ---------------------------------------------------------------------------#

def _is_harvester_worker() -> bool:
    """Return ``True`` iff the current worker is subscribed to the *harvester* queue.

    We inspect the command‑line (``-Q/--queues``) and fall back to the optional
    ``HARVESTER_WORKER`` env var.  This prevents default‑queue workers from
    pre‑warming a heavyweight Playwright browser.
    """
    # 1. Command‑line flags
    for idx, arg in enumerate(sys.argv):
        if arg in ("-Q", "--queues") and idx + 1 < len(sys.argv):
            queues = {q.strip() for q in sys.argv[idx + 1].split(",")}
            if QUEUE_NAME in queues:
                return True

    # 2. Environment override
    flag = os.environ.get("HARVESTER_WORKER", "").lower()
    if flag in {"1", "true", "yes"}:
        return True

    return False


# ---------------------------------------------------------------------------#
# Lifecycle hooks – run once per worker process
# ---------------------------------------------------------------------------#
@worker_process_init.connect
def _on_worker_start(**_) -> None:  # noqa: D401
    import multiprocessing as _mp

    _mp.current_process().daemon = False

    # Only pre‑warm the browser on *harvester* workers
    if _is_harvester_worker():
        _holder.get()  # forces initial creation
        logger.info("Browser ready for harvester worker PID %s", os.getpid())
    else:
        logger.info("PID %s – not a harvester queue worker; browser init skipped", os.getpid())


@worker_process_shutdown.connect
def _on_worker_shutdown(**_) -> None:  # noqa: D401
    """Gracefully tear down the browser when the worker exits."""
    logger.info("PID %s – shutting down browser (if any)", os.getpid())
    _holder.close()


# ---------------------------------------------------------------------------#
# Task base class
# ---------------------------------------------------------------------------#
class BrowserTask(Task):
    """Adds a .browser property giving the per-worker Browser instance."""

    abstract = True

    @property
    def browser(self) -> Browser:  # noqa: D401 – property name is fine
        return _holder.get()


# ---------------------------------------------------------------------------#
# Helper task: re‑prime the browser after a token fetch
# ---------------------------------------------------------------------------#
@shared_task(
    name="tasks.inyo._prep_browser_v3",
    base=BrowserTask,
    bind=True,
    max_retries=0,
    acks_late=False,
    ignore_result=True,
    queue=QUEUE_NAME,  # ─ Route to dedicated queue
)
def _prep_browser_v3(self) -> None:  # noqa: D401
    """Run :pymeth:`Browser.prep_v3` in the *same* (Celery) thread.

    Playwright is **not** thread‑safe, so we cannot call this method from a
    background ``threading.Thread``.  Scheduling it as its own Celery task lets
    the worker execute it asynchronously *after* our caller has already
    returned the token, without risking cross‑thread issues.
    """
    try:
        self.browser.prep_v3()
    except Exception as exc:  # pragma: no‑cover – we log but don't retry
        logger.warning("Background browser prep failed: %s", exc)


# ---------------------------------------------------------------------------#
# Actual task
# ---------------------------------------------------------------------------#
@shared_task(
    name="tasks.inyo.get_v3_token",
    base=BrowserTask,
    bind=True,
    max_retries=2,
    default_retry_delay=15,
    queue=QUEUE_NAME,  # ─ Route to dedicated queue
)
def get_v3_token(self) -> str:  # noqa: D401 – task name
    """Return a fresh ReCAPTCHA‑v3 token and queue background preparation."""
    try:
        token = self.browser.get_v3()

        # Fire‑and‑forget – schedule *without* blocking this task's return.
        _prep_browser_v3.delay()

        return token
    except Exception as exc:  # pragma: no‑cover
        logger.error("Token fetch failed: %s", exc)
        raise self.retry(exc=exc)
