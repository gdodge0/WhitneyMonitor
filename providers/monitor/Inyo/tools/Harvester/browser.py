from __future__ import annotations

import datetime
import math
import random
import time
from typing import Final, Tuple, List

from playwright.sync_api import (
    sync_playwright,
    Playwright,
    Browser as _PWB,
    Page,
)

WINDOW_SIZE_X: Final[int] = 1920
WINDOW_SIZE_Y: Final[int] = 1080


class Browser:
    """Headless-friendly helper around Playwright Chromium.

    The public API mirrors the older Selenium implementation so callers
    can drop-in-replace without changes.
    """

    _LAUNCH_ARGS: Final[List[str]] = [
        "--headless=chrome",
        "--use-gl=egl",
        "--enable-gpu",
        "--enable-webgl",
        "--ignore-gpu-blocklist",
        "--no-sandbox",
    ]

    def __init__(self, *, headless: bool = True) -> None:  # noqa: D401
        self._pw: Playwright = sync_playwright().start()
        self._browser: _PWB = self._pw.chromium.launch(
            headless=headless,
            args=self._LAUNCH_ARGS,
        )
        self._context = self._browser.new_context(
            viewport={"width": WINDOW_SIZE_X, "height": WINDOW_SIZE_Y},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
        )
        self._page: Page = self._context.new_page()

        # -----------------------------------------------------------------
        # Mouse position register: pick a starting coordinate and move the
        # pointer there immediately so that Playwright’s internal state
        # reflects it.  This position is updated by all helpers that move
        # the mouse.
        # -----------------------------------------------------------------
        self._mouse_pos: Tuple[int, int] = (
            random.randint(0, WINDOW_SIZE_X - 1),
            random.randint(0, WINDOW_SIZE_Y - 1),
        )
        self._page.mouse.move(*self._mouse_pos)

        # --- metrics for potential recycling by caller ------------------
        self.created_at: datetime.datetime = datetime.datetime.now(datetime.UTC)
        self.use_count: int = 0

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def prep_v3(self) -> None:  # noqa: D401
        """Navigate to the Recreation.gov page"""
        self._page.goto(
            "https://www.recreation.gov/permits/445860"
        )
        self._page.wait_for_load_state(timeout=10000)

    def get_v3(self) -> str:  # noqa: D401
        """Return a reCAPTCHA enterprise V3 token string."""

        # Re-prep browser
        if self._page.url != "https://www.recreation.gov/permits/445860":
            self.prep_v3()

        # Navigate to page and solve
        self._page.get_by_title("Explore Available Permits").click()
        self._page.wait_for_load_state(timeout=10000)
        return self._execute_recaptcha()

    # ------------------------------------------------------------------ #
    # Private Helpers
    # ------------------------------------------------------------------ #

    def _execute_recaptcha(self, max_retries: int = 50, delay: float = 0.1):
        """
        Synchronously invoke window.grecaptcha.enterprise.execute(...) with retries.

        Args:
            max_retries (int): Maximum attempts before giving up.
            delay (float): Seconds to wait between attempts (0.1 s = 100 ms).

        Returns:
            The value returned by grecaptcha.enterprise.execute.

        Raises:
            The last exception raised if all retries fail.
        """
        last_exc = None
        for _ in range(max_retries):
            try:
                return self._page.evaluate(
                    """() => window.grecaptcha.enterprise.execute(
                            1, { action: 'LBEAvailabilityPage' })"""
                )
            except Exception as e:
                last_exc = e
                time.sleep(delay)

        # Exhausted attempts
        raise last_exc

    # ------------------------------------------------------------------ #
    # Context-manager & cleanup
    # ------------------------------------------------------------------ #

    def close(self) -> None:  # noqa: D401
        try:
            self._browser.close()
        finally:
            self._pw.stop()

    def __enter__(self) -> "Browser":  # noqa: D401
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: D401
        self.close()

    # Convenience aliases -----------------------------------------------
    @property
    def page(self) -> Page:  # noqa: D401
        return self._page

    @property
    def driver(self) -> Page:  # noqa: D401
        return self._page


__all__: Final[List[str]] = ["Browser"]
