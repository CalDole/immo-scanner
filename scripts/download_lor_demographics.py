"""Download every Einwohnerregister dataset of the Amt für Statistik per LOR-Planungsraum.

Sources:
- all "Einwohner..." / "Ausländische Einwohner..." datasets on daten.berlin.de (CKAN), with
  their metadata PDFs,
- files on the AfS /opendata/ path that CKAN does not list, found by probing the known
  naming schemes for every half-year since 2021,
- the Statistischer Bericht A I 16 workbooks linked on the AfS report page.

Files go to data/reference/ewr_lor/ with a manifest.csv. Existing files are skipped.

    .venv\\Scripts\\python.exe scripts\\download_lor_demographics.py [--no-probe]
"""

import argparse
import csv
import re
import sys
from pathlib import Path

import httpx
from playwright.sync_api import BrowserContext, Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "data" / "reference" / "ewr_lor"
CKAN = "https://datenregister.berlin.de/api/3/action/"
OPENDATA = "https://www.statistik-berlin-brandenburg.de/opendata/"
REPORT_PAGE = "https://www.statistik-berlin-brandenburg.de/a-i-16-hj"
USER_AGENT = "Mozilla/5.0 (immo-scanner reference data)"


def ckan_resources() -> list[dict]:
    # package_search ignores paging and ranks poorly, so filter the full package list instead.
    with httpx.Client(timeout=120) as client:
        names = client.get(CKAN + "package_list").json()["result"]
        candidates = [n for n in names if re.match(r"(einwohner|ausl)", n)]
        packages = [client.get(CKAN + "package_show", params={"id": n}).json()["result"]
                    for n in candidates]
    items = {}
    for package in packages:
        title = package["title"]
        if not title.startswith(("Einwohner", "Ausländ")) or "LOR-Planungsr" not in title:
            continue
        for resource in package["resources"]:
            url = resource["url"].strip()
            name = url.rsplit("/", 1)[-1]
            items.setdefault(name, {"file": name, "url": url, "source": "daten.berlin.de",
                                    "title": title})
    return sorted(items.values(), key=lambda i: i["file"])


def probe_candidates(known: set[str]) -> list[dict]:
    names = []
    for year in range(2021, 2027):
        for month in ("06", "12"):
            date = f"{year}{month}"
            names += [f"EWR_L21_{date}{kind}_Matrix.csv" for kind in "EAHMDW"]
            names += [f"EWR{date}E_Matrix.csv", f"EWRMIGRA{date}H_Matrix.csv",
                      f"EWR_L21_MIGRA{date}H_Matrix.csv"]
        names += [f"WHNDAUER{year}_Matrix.csv", f"WHNLAGE{year}_Matrix.csv",
                  f"WHNDAUER_L21_{year}_Matrix.csv", f"WHNLAGE_L21_{year}_Matrix.csv"]
    return [{"file": n, "url": OPENDATA + n, "source": "AfS opendata (probed)", "title": ""}
            for n in names if n not in known]


class Timeout(Exception):
    pass


class OpendataFetcher:
    """The AfS site is a single-page app; /opendata/ files only resolve in a browser.

    A page that has handled a download sometimes stops reacting to further navigations, so
    every file gets a fresh page.
    """

    def __init__(self, context: BrowserContext):
        self.context = context
        self.page: Page | None = None
        self.name = ""
        self.download = None
        self.resolved = None
        self.missing = False

    def _new_page(self) -> Page:
        if self.page is not None:
            self.page.close()
        page = self.context.new_page()
        page.on("download", self._on_download)
        page.on("request", self._on_request)
        self.page = page
        return page

    def _on_download(self, download) -> None:
        # A late download from the previous file can arrive on the next page.
        if download.url.endswith("/" + self.name):
            self.download = download
        else:
            download.cancel()

    def _on_request(self, request) -> None:
        url = request.url
        if "status-code=404" in url or url.rstrip("/").endswith("404-fehlerseite"):
            self.missing = True
        elif url.startswith("https://download.statistik-berlin-brandenburg.de/") and url.endswith(
            "/" + self.name
        ):
            self.resolved = url

    def fetch(self, url: str, target: Path, attempts: int = 3) -> str | None:
        """Save `url` to `target`; return the resolved download URL, or None if missing."""
        for _ in range(attempts):
            try:
                return self._fetch_once(url, target)
            except Timeout:
                continue
        raise Timeout(url)

    def _fetch_once(self, url: str, target: Path, timeout_ms: int = 30_000) -> str | None:
        page = self._new_page()
        self.name = url.rsplit("/", 1)[-1]
        self.download, self.resolved, self.missing = None, None, False
        try:
            page.goto(url, wait_until="commit")
        except Exception:
            pass  # navigation aborts when it turns into a download
        waited = 0
        while waited < timeout_ms:
            if self.resolved is not None:
                try:
                    fetch_direct(self.resolved, target)
                    return self.resolved
                except Exception:
                    self.resolved = None
            if self.download is not None:
                # Download.save_as blocks forever when the browser download stalls.
                url_found = self.download.url
                self.download.cancel()
                fetch_direct(url_found, target)
                return url_found
            if self.missing:
                return None
            page.wait_for_timeout(250)
            waited += 250
        raise Timeout(url)


def report_workbooks(page: Page) -> list[dict]:
    page.goto(REPORT_PAGE, wait_until="networkidle", timeout=90_000)
    page.wait_for_timeout(2000)
    links = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    return [
        {"file": url.rsplit("/", 1)[-1], "url": url, "source": "AfS Statistischer Bericht A I 16",
         "title": "Einwohnerbestand Berlin - LOR-Planungsräume"}
        for url in dict.fromkeys(links)
        if re.search(r"SB_A01-16-00_\d{4}h0\d_BE\.(xlsx|pdf)$", url)
    ]


def fetch_direct(url: str, target: Path) -> None:
    with httpx.stream("GET", url, follow_redirects=True, timeout=300,
                      headers={"User-Agent": USER_AGENT}) as response:
        response.raise_for_status()
        if "text/html" in response.headers.get("content-type", ""):
            raise RuntimeError("got HTML instead of a file")
        tmp = target.with_suffix(target.suffix + ".part")
        with tmp.open("wb") as fh:
            for chunk in response.iter_bytes(1 << 16):
                fh.write(chunk)
        tmp.replace(target)


def date_mismatch(target: Path) -> str | None:
    """Return the ZEIT found in a matrix CSV if it contradicts the date in its file name."""
    match = re.search(r"(20\d\d)(06|12)?[A-Z]?_Matrix\.csv$", target.name)
    if not match:
        return None
    with target.open(encoding="latin-1") as fh:
        header = fh.readline().strip().split(";")
        first = fh.readline().strip().split(";")
    if "ZEIT" not in header:
        return None
    zeit = first[header.index("ZEIT")]
    return None if zeit.startswith(match.group(1) + (match.group(2) or "")) else zeit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-probe", action="store_true", help="skip probing unlisted files")
    args = parser.parse_args()
    TARGET.mkdir(parents=True, exist_ok=True)

    items = ckan_resources()
    print(f"{len(items)} files listed on daten.berlin.de")
    if not args.no_probe:
        items += probe_candidates({i["file"] for i in items})

    manifest, unresolved = [], []
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True, channel="msedge")
        except Exception:
            browser = pw.chromium.launch(headless=True)
        try:
            context = browser.new_context(accept_downloads=True)
            report_page = context.new_page()
            items += report_workbooks(report_page)
            report_page.close()
            fetcher = OpendataFetcher(context)
            for n, item in enumerate(items, 1):
                target = TARGET / item["file"]
                probed = item["source"].endswith("(probed)")
                if target.exists():
                    status = "cached"
                elif item["url"].startswith(OPENDATA):
                    print(f"[{n}/{len(items)}] checking {item['file']}")
                    try:
                        resolved = fetcher.fetch(item["url"], target)
                    except Timeout:
                        print(f"[{n}/{len(items)}] UNRESOLVED {item['file']}")
                        unresolved.append(item["file"])
                        continue
                    if resolved is None:
                        if not probed:
                            print(f"[{n}/{len(items)}] MISSING {item['file']}")
                        continue
                    item["url"] = resolved
                    status = "downloaded"
                    if zeit := date_mismatch(target):
                        print(f"[{n}/{len(items)}] WRONG DATE {item['file']} has ZEIT {zeit}")
                        target.unlink()
                        unresolved.append(item["file"])
                        continue
                else:
                    try:
                        fetch_direct(item["url"], target)
                        status = "downloaded"
                    except Exception as exc:
                        print(f"[{n}/{len(items)}] FAILED {item['file']}: {exc}")
                        continue
                print(f"[{n}/{len(items)}] {status} {item['file']}")
                manifest.append({**item, "bytes": target.stat().st_size})
        finally:
            browser.close()

    manifest_path = TARGET / "manifest.csv"
    rows = {}
    if manifest_path.exists():
        with manifest_path.open(encoding="utf-8") as fh:
            rows = {r["file"]: r for r in csv.DictReader(fh) if (TARGET / r["file"]).exists()}
    rows.update({m["file"]: m for m in manifest})
    with manifest_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["file", "bytes", "source", "title", "url"])
        writer.writeheader()
        writer.writerows(sorted(rows.values(), key=lambda m: m["file"]))
    if unresolved:
        print(f"No answer from the portal for: {', '.join(unresolved)}")
    print(f"{len(manifest)} files in {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
