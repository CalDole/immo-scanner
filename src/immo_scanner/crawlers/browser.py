"""Shared Playwright browser with a persistent profile.

A persistent profile keeps cookies (consent banners, bot-protection clearance) between
runs, which makes protected sites like ImmoScout24 far less likely to show captchas.
"""

import base64
import logging
import random
import time
from typing import Self

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

from immo_scanner.config import Settings

log = logging.getLogger(__name__)


class Browser:
    def __init__(self, settings: Settings, profile_name: str = "default") -> None:
        self.settings = settings
        self.profile_dir = settings.browser_profile_dir / profile_name
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    def __enter__(self) -> Self:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=self.settings.browser_headless,
            locale="de-DE",
            timezone_id="Europe/Berlin",
            viewport={"width": 1366, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._context is not None:
            self._context.close()
        if self._playwright is not None:
            self._playwright.stop()

    @property
    def page(self) -> Page:
        assert self._page is not None, "Browser not started"
        return self._page

    def pause(self) -> None:
        time.sleep(random.uniform(self.settings.crawl_delay_min, self.settings.crawl_delay_max))

    def goto(self, url: str, wait_for: str | None = None, timeout_ms: int = 45_000) -> str:
        self.pause()
        log.debug("Browser GET %s", url)
        self.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if wait_for:
            try:
                self.page.wait_for_selector(wait_for, timeout=timeout_ms)
            except Exception:
                log.warning("Selector %r did not appear on %s", wait_for, url)
        self.accept_cookies()
        return self.page.content()

    def fetch_html(self, url: str) -> str:
        """Fetch a page with the browser's cookies but without navigating to it.

        Only works for URLs on the origin of the current page. Much faster than `goto`
        for server-rendered pages, and it keeps the bot-protection clearance that a
        plain HTTP client does not have.
        """
        self.pause()
        log.debug("Browser fetch %s", url)
        return self.page.evaluate(
            """async url => {
                const response = await fetch(url, {credentials: 'include'});
                if (!response.ok) throw new Error('HTTP ' + response.status);
                return await response.text();
            }""",
            url,
        )

    def fetch_bytes(self, url: str, timeout_ms: int = 60_000) -> bytes:
        """Download a file with the browser's session.

        Bot protection rejects Playwright's own request context, so same-origin files are
        read with a `fetch` inside the page. Cross-origin ones (CDN-hosted PDFs) cannot be
        read that way because of CORS, and go through the request context instead.
        """
        encoded = self.page.evaluate(
            """async url => {
                try {
                    const response = await fetch(url, {credentials: 'include'});
                    if (!response.ok) return null;
                    const bytes = new Uint8Array(await response.arrayBuffer());
                    let binary = '';
                    for (const byte of bytes) binary += String.fromCharCode(byte);
                    return btoa(binary);
                } catch {
                    return null;
                }
            }""",
            url,
        )
        if encoded is not None:
            return base64.b64decode(encoded)
        response = self.page.request.get(url, timeout=timeout_ms)
        if not response.ok:
            raise RuntimeError(f"HTTP {response.status} for {url}")
        return response.body()

    def accept_cookies(self) -> None:
        candidates = [
            self.page.locator('#cc-main button[data-role="all"]'),
            *(
                self.page.get_by_role("button", name=label)
                for label in (
                    "Alle akzeptieren",
                    "Akzeptieren",
                    "Alle Cookies akzeptieren",
                    "Zustimmen",
                )
            ),
        ]
        for button in candidates:
            try:
                if button.count() and button.first.is_visible():
                    button.first.click(timeout=2_000)
                    return
            except Exception:
                continue

    def wait_for_human(self, check, message: str, timeout_s: int = 300) -> bool:
        """Poll `check()` while a human solves a captcha in the visible browser."""
        if self.settings.browser_headless:
            return bool(check())
        log.warning(
            "%s - please resolve it in the browser window (waiting %ds)", message, timeout_s
        )
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if check():
                return True
            time.sleep(3)
        return False
