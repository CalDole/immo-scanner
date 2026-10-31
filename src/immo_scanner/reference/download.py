import logging
from pathlib import Path

import httpx

from immo_scanner.config import get_settings

log = logging.getLogger(__name__)


class DownloadError(RuntimeError):
    pass


def download(url: str, target: Path, force: bool = False) -> Path:
    """Download `url` to `target`, falling back to a headless browser.

    The Amt für Statistik portal is a single-page app that only resolves /opendata/
    files in the browser; plain HTTP clients get an HTML shell back.
    """
    if target.exists() and not force:
        log.info("Using cached %s", target)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    settings = get_settings()

    log.info("Downloading %s", url)
    with httpx.stream(
        "GET",
        url,
        follow_redirects=True,
        timeout=600,
        headers={"User-Agent": settings.http_contact_agent},
    ) as response:
        response.raise_for_status()
        is_html = "text/html" in response.headers.get("content-type", "")
        if not is_html:
            with tmp.open("wb") as fh:
                for chunk in response.iter_bytes(1 << 16):
                    fh.write(chunk)
            tmp.replace(target)
            return target

    log.info("Got an HTML page instead of a file, retrying with a browser")
    try:
        _download_with_browser(url, target)
    except Exception as exc:
        raise DownloadError(
            f"Could not download {url} automatically ({exc}). Open the URL in your browser, "
            f"save the file as {target} and run the command again, or pass --file."
        ) from exc
    return target


def _download_with_browser(url: str, target: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_context(accept_downloads=True).new_page()
            with page.expect_download(timeout=90_000) as info:
                try:
                    page.goto(url, wait_until="commit")
                except Exception:
                    # Chromium aborts navigation when it turns into a download.
                    pass
            info.value.save_as(target)
        finally:
            browser.close()
