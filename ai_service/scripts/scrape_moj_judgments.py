#!/usr/bin/env python3
"""Step 1: Scrape judicial decisions from the MOJ (Ministry of Justice) API.

Paginates the judgements-list endpoint, then fetches full details for each
judgment. Strips HTML from the ruling text and writes one JSONL line per
judgment.

Usage:
    # Test with 5 pages first
    python -m ai_service.scripts.scrape_moj_judgments --max-pages 5

    # Full run (all ~2590 pages, ~31K judgments)
    python -m ai_service.scripts.scrape_moj_judgments

    # Filter to commercial court only (courtType=1)
    python -m ai_service.scripts.scrape_moj_judgments --court-type 1

Resume support: if the output file already exists, already-scraped IDs are
skipped automatically.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from loguru import logger

from ai_service.scripts._shared.paths import (
    ERRORS_JSONL,
    JUDGMENTS_JSONL,
    MOJ_DETAILS_URL,
    MOJ_LIST_URL,
    ensure_dirs,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# MOJ gateway returns HTTP 200 + HTML "Request Rejected" for non-browser
# User-Agents on get-details; judgements-list is more permissive.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _moj_session_headers() -> dict[str, str]:
    # Do not set Content-Type on the session: it would be sent on GET
    # (get-details) and MOJ's WAF rejects that with HTML "Request Rejected".
    return {
        "Accept": "application/json",
        "User-Agent": _BROWSER_UA,
        "Accept-Language": "ar-SA,ar;q=0.9,en;q=0.8",
        "Origin": "https://laws.moj.gov.sa",
        "Referer": "https://laws.moj.gov.sa/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
    }


_SESSION = requests.Session()
_SESSION.headers.update(_moj_session_headers())


def _reset_moj_session() -> None:
    """New TCP pool + cookies; helps after sustained WAF / rate-limit blocks."""
    global _SESSION
    _SESSION.close()
    _SESSION = requests.Session()
    _SESSION.headers.update(_moj_session_headers())

_REQUEST_TIMEOUT = 60


def _parse_moj_json(resp: requests.Response) -> dict:
    """Raise if the body is WAF HTML, empty, or not JSON (common when rate-limited)."""
    resp.raise_for_status()
    text = (resp.text or "").strip()
    head = text[:500].lower()
    if not text or text.startswith("<") or "request rejected" in head:
        raise RuntimeError(
            "non-JSON response (rate limit / WAF?): "
            + repr(text[:160])
        )
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"invalid JSON: {e}") from e


def _backoff_seconds(attempt: int, cap: int = 120) -> int:
    """attempt is 0-based; first retry waits ~4s."""
    return min(cap, 4 * (2**attempt))


def _is_waf_block_error(exc: BaseException) -> bool:
    s = str(exc).lower()
    return "request rejected" in s or "non-json response" in s


def _strip_html(html: str | None) -> str:
    """Convert HTML to plain text, preserving line breaks."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    # Replace <br> with newline before extracting text
    for br in soup.find_all("br"):
        br.replace_with("\n")
    text = soup.get_text(separator="\n")
    # Collapse multiple blank lines
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _fetch_list_page(
    page: int,
    page_size: int = 12,
    court_type: int | None = None,
    max_attempts: int = 8,
) -> dict:
    """Fetch a single page from the judgements-list API with retries."""
    body: dict = {"pageNumber": page, "pageSize": page_size}
    if court_type is not None:
        body["courtType"] = court_type

    last_err: Exception | None = None
    for attempt in range(max_attempts):
        try:
            resp = _SESSION.post(
                MOJ_LIST_URL, json=body, timeout=_REQUEST_TIMEOUT
            )
            return _parse_moj_json(resp)
        except Exception as e:
            last_err = e
            if attempt >= max_attempts - 1:
                break
            wait = _backoff_seconds(attempt)
            logger.warning(
                f"judgements-list page={page} attempt {attempt + 1}/"
                f"{max_attempts} failed ({e}); sleeping {wait}s"
            )
            time.sleep(wait)
    assert last_err is not None
    raise last_err


def _fetch_details_attempts(
    judgment_id: str,
    max_attempts: int,
) -> dict:
    """Full get-details retry loop using the current global session."""
    params = {"id": judgment_id, "lang": "ar", "IdentityNumber": ""}
    last_err: Exception | None = None
    for attempt in range(max_attempts):
        try:
            resp = _SESSION.get(
                MOJ_DETAILS_URL, params=params, timeout=_REQUEST_TIMEOUT
            )
            return _parse_moj_json(resp)
        except Exception as e:
            last_err = e
            if attempt >= max_attempts - 1:
                break
            wait = _backoff_seconds(attempt, cap=90)
            logger.warning(
                f"get-details id={judgment_id[:20]}... "
                f"attempt {attempt + 1}/{max_attempts} failed ({e}); "
                f"sleeping {wait}s"
            )
            time.sleep(wait)
    assert last_err is not None
    raise last_err


def _fetch_details(
    judgment_id: str,
    max_attempts: int = 7,
    waf_cooldown: float = 600.0,
) -> dict:
    """Fetch details; optional long cooldown + fresh session if WAF blocks."""
    try:
        return _fetch_details_attempts(judgment_id, max_attempts)
    except Exception as e:
        if waf_cooldown <= 0 or not _is_waf_block_error(e):
            raise
        logger.warning(
            f"get-details WAF block for id={judgment_id[:20]}... "
            f"after {max_attempts} attempts; sleeping {waf_cooldown:.0f}s, "
            "resetting HTTP session, then retrying the same backoff cycle once"
        )
        time.sleep(waf_cooldown)
        _reset_moj_session()
        return _fetch_details_attempts(judgment_id, max_attempts)


def _load_existing_ids(output_path: Path) -> set[str]:
    """Load IDs already written to the output JSONL for resume support."""
    ids: set[str] = set()
    if output_path.exists():
        with open(output_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if "id" in obj:
                        ids.add(obj["id"])
                except json.JSONDecodeError:
                    continue
    if ids:
        logger.info(f"Resume: found {len(ids)} already-scraped judgments")
    return ids


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape MOJ judicial decisions for BGE fine-tuning."
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(JUDGMENTS_JSONL),
        help=f"Output JSONL path (default: {JUDGMENTS_JSONL})",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Max pages to scrape (0 = all). Default: 0",
    )
    parser.add_argument(
        "--start-page",
        type=int,
        default=1,
        help="Page to start from. Default: 1",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.75,
        help="Delay between detail requests in seconds. Default: 0.75",
    )
    parser.add_argument(
        "--page-gap",
        type=float,
        default=2.0,
        help="Pause after each list page (reduces rate limits). Default: 2.0",
    )
    parser.add_argument(
        "--list-fail-sleep",
        type=float,
        default=60.0,
        help="Seconds to sleep after a list page fails all retries. Default: 60",
    )
    parser.add_argument(
        "--waf-cooldown",
        type=float,
        default=600.0,
        help="After get-details hits WAF for all short retries, sleep this many "
        "seconds, reset the session, then run one full retry cycle. 0=off. "
        "Default: 600",
    )
    parser.add_argument(
        "--court-type",
        type=int,
        default=None,
        help="Filter by court type (e.g., 1=commercial). Default: all",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=12,
        help="Items per page. Default: 12",
    )
    args = parser.parse_args()

    ensure_dirs()
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    existing_ids = _load_existing_ids(output_path)
    errors_path = ERRORS_JSONL

    # Discover total pages
    logger.info("Fetching first page to discover total count...")
    first_page = _fetch_list_page(
        args.start_page,
        page_size=args.page_size,
        court_type=args.court_type,
    )
    model = first_page.get("model", {})
    total_count = model.get("totalCount", 0)
    total_pages = model.get("totalPages", 0)
    logger.info(f"Total judgments: {total_count}, Total pages: {total_pages}")

    end_page = total_pages
    if args.max_pages > 0:
        end_page = min(args.start_page + args.max_pages - 1, total_pages)
    logger.info(f"Will scrape pages {args.start_page} to {end_page}")

    scraped_count = 0
    skipped_count = 0
    error_count = 0

    with open(output_path, "a", encoding="utf-8") as out_f, \
         open(errors_path, "a", encoding="utf-8") as err_f:

        for page_num in range(args.start_page, end_page + 1):
            try:
                if page_num == args.start_page:
                    page_data = first_page
                else:
                    page_data = _fetch_list_page(
                        page_num,
                        page_size=args.page_size,
                        court_type=args.court_type,
                    )
            except Exception as e:
                logger.error(f"Failed to fetch page {page_num}: {e}")
                error_count += 1
                time.sleep(args.list_fail_sleep)
                continue

            judgments = page_data.get("model", {}).get(
                "judgementsCollection", []
            )
            if not judgments:
                logger.warning(f"Page {page_num}: empty collection")
                continue

            for item in judgments:
                jid = item.get("id", "")
                if not jid:
                    continue

                if jid in existing_ids:
                    skipped_count += 1
                    continue

                # Fetch details
                try:
                    details_resp = _fetch_details(
                        jid,
                        waf_cooldown=args.waf_cooldown,
                    )
                    detail = details_resp.get("model", {})
                except Exception as e:
                    logger.warning(f"Failed details for {jid}: {e}")
                    err_f.write(json.dumps({
                        "id": jid,
                        "error": str(e),
                        "page": page_num,
                    }, ensure_ascii=False) + "\n")
                    error_count += 1
                    # Longer pause after detail failures (often rate-limit bursts)
                    time.sleep(max(args.delay, 8.0))
                    continue

                # Extract and clean
                plain_text = _strip_html(
                    detail.get("judgmentTextofRulling", "")
                )

                record = {
                    "id": jid,
                    "title": detail.get("title", item.get("judgementNumber", "")),
                    "caseNumber": item.get("caseNumber", detail.get("judgmentNumber", "")),
                    "courtType": item.get("courtType", detail.get("courtType", "")),
                    "courtName": item.get("courtName", detail.get("courtName", "")),
                    "city": item.get("city", detail.get("city", "")),
                    "hijriYear": item.get("hijriYear", detail.get("hjriiYear", "")),
                    "judgmentDate": detail.get("judgmentDate", ""),
                    "judgmentHijriDate": detail.get("judgmentHijriiDate", item.get("judgementDate", "")),
                    "plain_text": plain_text,
                }

                out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                out_f.flush()
                existing_ids.add(jid)
                scraped_count += 1

                if scraped_count % 50 == 0:
                    logger.info(
                        f"Progress: {scraped_count} scraped, "
                        f"{skipped_count} skipped, {error_count} errors "
                        f"(page {page_num}/{end_page})"
                    )

                time.sleep(args.delay)

            time.sleep(args.page_gap)

    logger.info(
        f"Done! Scraped: {scraped_count}, Skipped: {skipped_count}, "
        f"Errors: {error_count}. Output: {output_path}"
    )


if __name__ == "__main__":
    main()
