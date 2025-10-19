import asyncio
import logging
import os
import random
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional
from urllib.parse import quote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from models import Listing, SearchResult
from providers.base import BaseProvider
from utils.datetime_utils import BERLIN, now_utc, to_utc_aware

logger = logging.getLogger(__name__)
@dataclass
class _PageParseResult(Sequence[Listing]):
    listings: List[Listing]
    items_total: int
    promoted_skipped: int

    def __len__(self) -> int:
        return len(self.listings)

    def __getitem__(self, index):
        return self.listings[index]


CONSENT_ID_MARKERS = (
    "gdpr-banner-title",
    "gdpr-banner-accept",
    "gdpr-banner-cmp-button",
)


class KleinanzeigenProvider(BaseProvider):
    """Provider for kleinanzeigen.de with polite throttling and CAPTCHA awareness"""

    BASE_URL = "https://www.kleinanzeigen.de"
    PLATFORM = "kleinanzeigen.de"

    CAPTCHA_MARKERS = (
        "ich bin kein roboter",
        "bitte verifizieren sie, dass sie kein roboter sind",
        "unser system hat ungewöhnliche aktivitäten",
        "aktion erforderlich",
        "sicherheitsprüfung",
        "zugriff verweigert",
        "recaptcha",
    )

    def __init__(self) -> None:
        self.enabled = os.environ.get("ENABLE_KLEINANZEIGEN", "true").strip().lower() not in {
            "0",
            "false",
            "off",
        }

        self.mode = os.environ.get("KLEINANZEIGEN_MODE", "playwright").strip().lower()
        if self.mode not in {"http", "playwright"}:
            logger.warning(
                {
                    "event": "ka_mode_invalid",
                    "provided": self.mode,
                    "fallback": "playwright",
                }
            )
            self.mode = "playwright"

        self.playwright_headless = self._env_flag("KLEINANZEIGEN_HEADLESS", True)
        self.playwright_timeout_ms = int(os.environ.get("KLEINANZEIGEN_TIMEOUT_MS", "20000"))
        self.block_if_consent_fail = self._env_flag(
            "KLEINANZEIGEN_BLOCK_IF_CONSENT_FAIL", True
        )

        self.base_delay = float(os.environ.get("KA_BASE_DELAY_SEC", "1.0"))
        self.baseline_delay = float(os.environ.get("KA_BASELINE_DELAY_SEC", "1.2"))
        self.max_retries = int(os.environ.get("KA_MAX_RETRIES", "3"))
        self.backoff_429_min = float(os.environ.get("KA_BACKOFF_429_MIN", "20"))
        self.backoff_403_hours = float(os.environ.get("KA_BACKOFF_403_HOURS", "6"))
        self.cooldown_on_captcha_min = float(os.environ.get("KA_COOLDOWN_ON_CAPTCHA_MIN", "45"))

        self.headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "de-DE,de;q=0.9",
            "Connection": "keep-alive",
            "Referer": "https://www.kleinanzeigen.de/",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }

        self.berlin_tz = BERLIN

        # Rate limiting & state
        self._rate_lock = asyncio.Lock()
        self._last_request_at: Optional[datetime] = None
        self._captcha_state: str = "clear"
        self._captcha_seen_at: Optional[datetime] = None
        self._cooldown_until: Optional[datetime] = None
        self._captcha_backoff_exp: int = 0
        self._pending_events: List[dict] = []
        self._last_error: Optional[str] = None
        self._session_warmed: bool = False
        self._consent_status: str = "not_detected"
        self._resolver_used: str = self.mode
        self._consent_detected_flag: bool = False
        self._consent_resolved_flag: bool = False

    @property
    def platform_name(self) -> str:
        return self.PLATFORM

    @staticmethod
    def detect_consent(html: Optional[str]) -> bool:
        if not html:
            return False

        soup = BeautifulSoup(html, "html.parser")
        for marker_id in CONSENT_ID_MARKERS:
            if soup.find(id=marker_id):
                return True

        has_cards = bool(
            soup.select("article[data-adid], article[data-id]")
        )

        if has_cards:
            return False

        lower = html.lower()
        return any(marker_id in lower for marker_id in CONSENT_ID_MARKERS)

    def _reset_run_state(self) -> None:
        self._session_warmed = False
        self._consent_detected_flag = False
        self._consent_resolved_flag = False
        self._consent_status = "not_detected"
        self._resolver_used = self.mode

    @staticmethod
    def _env_flag(name: str, default: bool) -> bool:
        raw = os.environ.get(name)
        if raw is None:
            return default
        value = raw.strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
        return default

    # ------------------------------------------------------------------
    # Public helpers (used by tests and services)
    # ------------------------------------------------------------------
    def build_search_url(self, keyword: str, page: int = 1) -> str:
        """Construct Kleinanzeigen search URL for keyword/page"""
        slug = self._slugify(keyword)
        if page <= 1:
            path = f"/s-{slug}/"
        else:
            path = f"/s-seite:{page}/{slug}/"
        return urljoin(self.BASE_URL, path)

    def _candidate_paths(self, keyword: str, page: int) -> List[str]:
        slug = self._slugify(keyword) or "suche"
        candidates: List[str] = []

        if page <= 1:
            raw = [
                f"/s-{slug}/",
                f"/s-{slug}/k0",
                f"/s-suchanfrage/{slug}/k0",
                f"/s/{slug}/k0",
            ]
        else:
            raw = [
                f"/s-seite:{page}/{slug}/",
                f"/s-seite:{page}/{slug}/k0",
                f"/s-seite:{page}/suchanfrage-{slug}/k0",
                f"/s/{slug}/k0/seite:{page}",
            ]

        for path in raw:
            if path not in candidates:
                candidates.append(path)

        return candidates

    # ------------------------------------------------------------------
    # BaseProvider implementation
    # ------------------------------------------------------------------
    async def _warmup_session(
        self, client: httpx.AsyncClient, mode: str, keyword: str
    ) -> bool:
        if self._session_warmed:
            return True

        delay = self.baseline_delay if mode == "baseline" else self.base_delay

        try:
            await self._respect_rate_limit(delay)
            response = await client.get(self.BASE_URL + "/")
        except Exception as exc:
            self._pending_events.append(
                {
                    "event": "ka_warmup_error",
                    "platform": self.platform_name,
                    "error": str(exc)[:200],
                }
            )
            logger.warning(
                {
                    "event": "ka_warmup_error",
                    "platform": self.platform_name,
                    "error": str(exc),
                }
            )
            self._last_error = str(exc)
            return False

        client.cookies.update(response.cookies)

        logger.info(
            {
                "event": "ka_first_get",
                "platform": self.platform_name,
                "status": response.status_code,
                "url": str(response.url),
                "via": "http",
                "q": keyword,
            }
        )

        consent_detected = self.detect_consent(response.text)
        self._consent_detected_flag = consent_detected

        if consent_detected:
            logger.info(
                {
                    "event": "ka_consent_detected",
                    "platform": self.platform_name,
                    "url": str(response.url),
                    "q": keyword,
                }
            )

            if self.mode == "playwright":
                resolved = await self._resolve_consent_playwright(
                    client, str(response.url), keyword=keyword
                )
                if resolved:
                    self._consent_resolved_flag = True
                    self._consent_status = "resolved"
                    self._resolver_used = "playwright"
                    self._last_error = None
                    self._session_warmed = True
                    return True

            snippet = self._sanitize_html_snippet(response.text)
            logger.warning(
                {
                    "event": "ka_consent_resolved",
                    "platform": self.platform_name,
                    "success": False,
                    "reason": (
                        "playwright_disabled"
                        if self.mode != "playwright"
                        else "playwright_failed"
                    ),
                    "url": str(response.url),
                    "q": keyword,
                    "page": None,
                    "snippet": snippet,
                }
            )
            self._consent_resolved_flag = False
            self._consent_status = "blocked"
            self._last_error = "Consent blockiert"
            if self.block_if_consent_fail:
                return False

        else:
            self._consent_resolved_flag = False
            self._consent_status = "not_detected"
            self._resolver_used = "http"
            self._last_error = None

        self._session_warmed = True
        return True

    async def _fetch_search_page(
        self,
        client: httpx.AsyncClient,
        keyword: str,
        page: int,
        mode: str,
    ) -> tuple[Optional[httpx.Response], Optional[int], Optional[str], Optional[str]]:
        candidates = self._candidate_paths(keyword, page)
        last_status: Optional[int] = None
        last_final_url: Optional[str] = None
        last_reason: Optional[str] = None

        for path in candidates:
            url = urljoin(self.BASE_URL, path)
            response, status, final_url, reason = await self._request(
                client,
                url,
                mode,
                keyword=keyword,
                page=page,
            )

            last_status = status
            last_final_url = final_url or url
            last_reason = reason

            logger.info(
                {
                    "event": "ka_search",
                    "platform": self.platform_name,
                    "q": keyword,
                    "url": url,
                    "status": status,
                    "final_url": last_final_url,
                    "page": page,
                }
            )

            if response is not None:
                return response, status, last_final_url, None

            if reason != "not_found":
                break

        return None, last_status, last_final_url, last_reason

    async def _resolve_consent_playwright(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        keyword: Optional[str] = None,
        page: Optional[int] = None,
    ) -> bool:
        try:
            from playwright.async_api import (  # type: ignore
                TimeoutError as PlaywrightTimeoutError,
                async_playwright,
            )
        except Exception as exc:  # pragma: no cover - optional dependency
            logger.warning(
                {
                    "event": "ka_consent_click_ok",
                    "platform": self.platform_name,
                    "success": False,
                    "error": str(exc),
                    "reason": "import_error",
                    "url": url,
                    "q": keyword,
                    "page": page,
                }
            )
            return False

        browser = None
        context = None

        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(
                    headless=self.playwright_headless
                )
                context = await browser.new_context(
                    user_agent=self.headers.get("User-Agent"),
                    locale="de-DE",
                )
                page_obj = await context.new_page()
                await page_obj.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self.playwright_timeout_ms,
                )

                try:
                    await page_obj.wait_for_selector(
                        "#gdpr-banner-accept",
                        timeout=self.playwright_timeout_ms,
                    )
                except PlaywrightTimeoutError:
                    logger.warning(
                        {
                            "event": "ka_consent_click_ok",
                            "platform": self.platform_name,
                            "success": False,
                            "reason": "no_banner",
                            "url": url,
                            "q": keyword,
                            "page": page,
                        }
                    )
                    return False

                try:
                    await page_obj.click(
                        "#gdpr-banner-accept",
                        timeout=self.playwright_timeout_ms,
                    )
                except Exception as exc:
                    logger.warning(
                        {
                            "event": "ka_consent_click_ok",
                            "platform": self.platform_name,
                            "success": False,
                            "error": str(exc),
                            "reason": "click_failed",
                            "url": url,
                            "q": keyword,
                            "page": page,
                        }
                    )
                    return False

                try:
                    await page_obj.wait_for_selector(
                        "#gdpr-banner-title",
                        state="detached",
                        timeout=self.playwright_timeout_ms,
                    )
                except PlaywrightTimeoutError:
                    logger.warning(
                        {
                            "event": "ka_consent_click_ok",
                            "platform": self.platform_name,
                            "success": False,
                            "reason": "banner_persist",
                            "url": url,
                            "q": keyword,
                            "page": page,
                        }
                    )
                    return False

                await page_obj.wait_for_timeout(300)

                cookies = await context.cookies()
                for cookie in cookies:
                    name = cookie.get("name")
                    if not name:
                        continue
                    value = cookie.get("value", "")
                    domain = cookie.get("domain") or "www.kleinanzeigen.de"
                    path = cookie.get("path", "/")
                    try:
                        client.cookies.set(name, value, domain=domain, path=path)
                    except Exception:
                        continue

                logger.info(
                    {
                        "event": "ka_consent_click_ok",
                        "platform": self.platform_name,
                        "success": True,
                        "url": url,
                        "q": keyword,
                        "page": page,
                        "via": "playwright",
                    }
                )

                logger.info(
                    {
                        "event": "ka_consent_resolved",
                        "platform": self.platform_name,
                        "success": True,
                        "url": url,
                        "q": keyword,
                        "page": page,
                    }
                )

                return True
        except Exception as exc:  # pragma: no cover - external dependency
            logger.warning(
                {
                    "event": "ka_consent_click_ok",
                    "platform": self.platform_name,
                    "success": False,
                    "error": str(exc),
                    "reason": "playwright_error",
                    "url": url,
                    "q": keyword,
                    "page": page,
                }
            )
            return False
        finally:
            try:
                if context is not None:
                    await context.close()
            except Exception:
                pass
            try:
                if browser is not None:
                    await browser.close()
            except Exception:
                pass

    async def search(
        self,
        keyword: str,
        since_ts: Optional[datetime] = None,
        sample_mode: bool = False,
        crawl_all: bool = False,
        max_pages_override: Optional[int] = None,
    ) -> SearchResult:
        mode = "baseline" if crawl_all else "poll"

        self._reset_run_state()
        metadata = self._build_metadata()

        if not self.enabled:
            metadata.update({"enabled": False})
            metadata["events"] = self._drain_events()
            return SearchResult(items=[], pages_scanned=0, metadata=metadata)

        if not self._can_attempt_request():
            metadata.update({
                "cooldown_active": True,
                "cooldown_until": self._cooldown_until.isoformat() if self._cooldown_until else None,
            })
            metadata["events"] = self._drain_events()
            return SearchResult(items=[], pages_scanned=0, metadata=metadata)

        max_pages = max_pages_override or (120 if crawl_all else 1)
        max_pages = max(1, min(max_pages, 200))

        items: List[Listing] = []
        seen_ids: set[str] = set()
        pages_scanned = 0
        has_more = False
        error_message: Optional[str] = None
        last_page_index: Optional[int] = None
        last_final_url: Optional[str] = None

        async with httpx.AsyncClient(headers=self.headers, timeout=30.0, follow_redirects=True) as client:
            prepared = await self._warmup_session(client, mode, keyword)

            if not prepared and self.block_if_consent_fail:
                metadata = self._build_metadata()
                events = self._drain_events()
                if events:
                    metadata["events"] = events
                if self._last_error:
                    metadata["last_error"] = self._last_error
                return SearchResult(items=[], pages_scanned=0, metadata=metadata)

            page = 1
            while page <= max_pages:
                response, status, final_url, reason = await self._fetch_search_page(
                    client, keyword, page, mode
                )
                last_final_url = final_url

                if response is None:
                    if reason == "not_found":
                        error_message = f"Suchpfad nicht gültig (HTTP {status}) – {final_url}"
                    elif reason == "captcha":
                        error_message = "Bot-Schutz aktiv – Kleinanzeigen hat die Anfrage blockiert."
                    elif reason == "consent":
                        error_message = "Consent-Seite blockiert die Suche."
                    elif reason == "block":
                        error_message = self._last_error or f"Anfrage blockiert (HTTP {status})."
                    elif reason == "network":
                        error_message = self._last_error or "Netzwerkfehler bei Kleinanzeigen."
                    else:
                        error_message = self._last_error or "Unbekannter Suchfehler bei Kleinanzeigen."
                    self._last_error = error_message
                    break

                html = response.text
                page_result = self._parse_search_page(html)
                page_items = list(page_result)
                items_total = page_result.items_total
                promoted_skipped = page_result.promoted_skipped
                organic_count = len(page_items)

                filtered: List[Listing] = []
                for listing in page_items:
                    if listing.platform_id in seen_ids:
                        continue
                    seen_ids.add(listing.platform_id)
                    filtered.append(listing)

                if filtered:
                    pages_scanned += 1
                    items.extend(filtered)
                    last_page_index = page
                    self._last_error = None

                logger.info(
                    {
                        "event": "ka_page",
                        "platform": self.platform_name,
                        "q": keyword,
                        "page": page,
                        "items_total": items_total,
                        "items_promoted_skipped": promoted_skipped,
                        "items_kept": len(filtered),
                        "url": final_url,
                    }
                )

                if not crawl_all and not sample_mode:
                    has_more = organic_count > 0 and page < max_pages
                    break

                has_more = organic_count > 0 and page < max_pages

                if organic_count == 0:
                    break

                page += 1

        metadata = self._build_metadata()
        events = self._drain_events()
        if events:
            metadata["events"] = events

        if last_final_url:
            metadata["last_search_url"] = last_final_url

        if error_message:
            metadata["last_error"] = error_message

        logger.info(
            {
                "event": "ka_search_summary",
                "platform": self.platform_name,
                "q": keyword,
                "pages": pages_scanned,
                "items": len(items),
                "has_more": has_more,
                "resolver": self._resolver_used,
                "consent_status": self._consent_status,
                "error": error_message,
            }
        )

        return SearchResult(
            items=items,
            has_more=has_more,
            pages_scanned=pages_scanned,
            last_page_index=last_page_index,
            metadata=metadata,
        )

    async def fetch_posted_ts_batch(self, items: List[Listing], concurrency: int = 4) -> None:
        """Fetch posted_ts for unseen Kleinanzeigen items"""

        if not items:
            return

        if not self._can_attempt_request():
            return

        semaphore = asyncio.Semaphore(max(1, min(concurrency, 3)))

        async with httpx.AsyncClient(headers=self.headers, timeout=30.0, follow_redirects=True) as client:
            async def fetch(item: Listing):
                if item.posted_ts is not None:
                    return
                url = item.url
                if not url.startswith(self.BASE_URL):
                    return
                async with semaphore:
                    response, _, _, reason = await self._request(
                        client,
                        url,
                        "detail",
                        keyword=None,
                        page=None,
                    )
                if response is None:
                    return
                posted = self._extract_posted_ts_from_detail(response.text)
                if posted:
                    item.posted_ts = posted

            tasks = [fetch(item) for item in items]
            await asyncio.gather(*tasks)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _build_metadata(self) -> dict:
        data = {
            "platform": self.platform_name,
            "enabled": self.enabled,
            "captcha_state": self._captcha_state,
            "cooldown_until": self._cooldown_until.isoformat() if self._cooldown_until else None,
            "last_error": self._last_error,
            "cooldown_active": self._cooldown_until is not None
            and now_utc() < self._cooldown_until,
            "resolver": self._resolver_used,
            "resolver_mode": self.mode,
            "consent_status": self._consent_status,
            "consent_detected": self._consent_detected_flag,
            "consent_resolved": self._consent_resolved_flag,
            "health_note": self._health_note(),
        }
        return data

    def _health_note(self) -> str:
        if not self.enabled:
            return "Deaktiviert"

        now = now_utc()
        if self._captcha_state == "entered":
            if self._cooldown_until and now < self._cooldown_until:
                minutes = int(max(1, (self._cooldown_until - now).total_seconds() // 60))
                return f"Captcha erkannt — Pause aktiv ({minutes} min)"
            return "Captcha erkannt — Pause beendet"

        if self._cooldown_until and now < self._cooldown_until:
            minutes = int(max(1, (self._cooldown_until - now).total_seconds() // 60))
            return f"Cooldown aktiv ({minutes} min)"

        if self._consent_status == "blocked":
            return "Consent blockiert"

        if self._consent_status == "resolved":
            if self._resolver_used == "playwright":
                return "Consent OK (Playwright)"
            return "Consent OK"

        if self._consent_status == "not_detected":
            return "Consent nicht erforderlich"

        return "Status unbekannt"

    def _drain_events(self) -> List[dict]:
        events = list(self._pending_events)
        self._pending_events.clear()
        return events

    def _can_attempt_request(self) -> bool:
        if self._cooldown_until is None:
            return True
        return now_utc() >= self._cooldown_until

    async def _respect_rate_limit(self, delay: float) -> None:
        delay = max(0.3, delay)
        async with self._rate_lock:
            now = now_utc()
            if self._last_request_at is not None:
                elapsed = (now - self._last_request_at).total_seconds()
                target = delay + random.uniform(0.0, 0.3)
                wait_for = target - elapsed
                if wait_for > 0:
                    await asyncio.sleep(wait_for)
            self._last_request_at = now_utc()

    @staticmethod
    def _sanitize_html_snippet(html: Optional[str], limit: int = 2048) -> str:
        if not html:
            return ""
        snippet = re.sub(r"\s+", " ", html)
        return snippet[:limit]

    async def _request(
        self,
        client: httpx.AsyncClient,
        url: str,
        mode: str,
        *,
        keyword: Optional[str] = None,
        page: Optional[int] = None,
    ) -> tuple[Optional[httpx.Response], Optional[int], Optional[str], Optional[str]]:
        if not url.startswith(self.BASE_URL):
            raise ValueError("Outbound URL not allowed")

        delay = self.baseline_delay if mode == "baseline" else self.base_delay
        await self._respect_rate_limit(delay)

        last_exc: Optional[Exception] = None
        consent_retry = False

        for attempt in range(self.max_retries):
            try:
                logger.info(
                    {
                        "event": "ka_request",
                        "platform": self.platform_name,
                        "url": url,
                        "attempt": attempt + 1,
                        "mode": mode,
                        "resolver": "http",
                    }
                )
                response = await client.get(url)
            except httpx.HTTPError as exc:
                last_exc = exc
                await asyncio.sleep(min(5, 1 + attempt))
                continue

            final_url = str(response.url)
            status = response.status_code
            text = response.text or ""
            text_lower = text.lower()

            if status in (403, 429, 503):
                self._handle_block(
                    status,
                    keyword=keyword,
                    page=page,
                    url=url,
                    final_url=final_url,
                )
                return None, status, final_url, "block"

            consent_present = self.detect_consent(text)
            if consent_present:
                self._consent_detected_flag = True
                if not consent_retry:
                    consent_retry = True
                    logger.info(
                        {
                            "event": "ka_consent_detected",
                            "platform": self.platform_name,
                            "q": keyword,
                            "page": page,
                            "url": final_url,
                        }
                    )

                    if self.mode == "playwright":
                        resolved = await self._resolve_consent_playwright(
                            client,
                            final_url,
                            keyword=keyword,
                            page=page,
                        )
                        if resolved:
                            self._consent_resolved_flag = True
                            self._consent_status = "resolved"
                            self._resolver_used = "playwright"
                            self._last_error = None
                            continue

                    snippet = self._sanitize_html_snippet(text)
                    logger.warning(
                        {
                            "event": "ka_consent_resolved",
                            "platform": self.platform_name,
                            "success": False,
                            "reason": (
                                "playwright_disabled"
                                if self.mode != "playwright"
                                else "playwright_failed"
                            ),
                            "url": final_url,
                            "q": keyword,
                            "page": page,
                            "snippet": snippet,
                        }
                    )
                    self._consent_resolved_flag = False
                    self._consent_status = "blocked"
                    self._last_error = "Consent blockiert"
                    if self.block_if_consent_fail:
                        return None, status, final_url, "consent"
                else:
                    if self.block_if_consent_fail:
                        return None, status, final_url, "consent"

            if self._detect_captcha(text_lower) or "/captcha" in final_url.lower():
                self._handle_captcha_detected(
                    keyword=keyword,
                    page=page,
                    url=url,
                    final_url=final_url,
                    status=status,
                )
                return None, status, final_url, "captcha"

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    return None, 404, final_url, "not_found"
                last_exc = exc
                break

            self._handle_captcha_recovered()
            self._last_error = None
            if self._consent_resolved_flag:
                self._consent_status = "resolved"
            elif self._consent_status not in {"blocked", "resolved"}:
                self._consent_status = "not_detected"
            if self._resolver_used != "playwright":
                self._resolver_used = "http"
            return response, status, final_url, None

        if last_exc:
            status = None
            final_url = url
            if isinstance(last_exc, httpx.HTTPStatusError):
                status = last_exc.response.status_code
                final_url = str(last_exc.response.url)
            self._last_error = str(last_exc)
            logger.error(
                {
                    "event": "ka_network_error",
                    "platform": self.platform_name,
                    "q": keyword,
                    "page": page,
                    "url": url,
                    "error": str(last_exc),
                }
            )
            return None, status, final_url, "network"

        return None, None, url, "network"

    def _handle_block(
        self,
        status_code: int,
        *,
        keyword: Optional[str] = None,
        page: Optional[int] = None,
        url: Optional[str] = None,
        final_url: Optional[str] = None,
    ) -> None:
        now = now_utc()
        if status_code == 429:
            minutes = max(self.backoff_429_min, 1.0)
            cooldown = timedelta(minutes=minutes * (2 ** min(self._captcha_backoff_exp, 3)))
            self._cooldown_until = now + cooldown
            self._last_error = f"HTTP 429 – cooldown {int(cooldown.total_seconds() // 60)} min"
        elif status_code == 403:
            hours = max(self.backoff_403_hours, 1.0)
            cooldown = timedelta(hours=hours)
            self._cooldown_until = now + cooldown
            self._last_error = "HTTP 403 – access denied"
        else:
            self._last_error = f"HTTP {status_code}"
        logger.warning(
            {
                "event": "ka_block",
                "platform": self.platform_name,
                "status": status_code,
                "cooldown_until": self._cooldown_until.isoformat() if self._cooldown_until else None,
                "q": keyword,
                "page": page,
                "url": final_url or url,
            }
        )

    def _handle_captcha_detected(
        self,
        *,
        keyword: Optional[str],
        page: Optional[int],
        url: Optional[str],
        final_url: Optional[str],
        status: Optional[int],
    ) -> None:
        now = now_utc()
        state_changed = self._captcha_state != "entered"

        if state_changed:
            self._captcha_backoff_exp = 0
            self._captcha_seen_at = now
        else:
            self._captcha_backoff_exp += 1

        cooldown_minutes = self.cooldown_on_captcha_min * (2 ** min(self._captcha_backoff_exp, 3))
        cooldown_minutes = max(self.cooldown_on_captcha_min, cooldown_minutes)
        cooldown_minutes = min(cooldown_minutes, 8 * 60)  # cap at 8 hours
        self._cooldown_until = now + timedelta(minutes=cooldown_minutes)
        self._captcha_state = "entered"
        self._last_error = "captcha_detected"

        if state_changed:
            event = {
                "event": "captcha_detected",
                "platform": self.platform_name,
                "state": "entered",
                "first_seen": now.isoformat(),
                "cooldown_minutes": int(cooldown_minutes),
                "cooldown_until": self._cooldown_until.isoformat() if self._cooldown_until else None,
            }
            self._pending_events.append(event)

        recaptcha_event = {
            "event": "ka_recaptcha",
            "platform": self.platform_name,
            "q": keyword,
            "page": page,
            "url": final_url or url,
            "status": status,
            "detected_at": now.isoformat(),
        }
        self._pending_events.append(recaptcha_event)

        captcha_log = {**recaptcha_event, "event": "ka_captcha_detected"}
        self._pending_events.append(captcha_log)
        logger.warning(captcha_log)

    def _handle_captcha_recovered(self) -> None:
        if self._captcha_state != "entered":
            return

        now = now_utc()
        event = {
            "event": "captcha_detected",
            "platform": self.platform_name,
            "state": "recovered",
            "first_seen": self._captcha_seen_at.isoformat() if self._captcha_seen_at else None,
            "recovered_at": now.isoformat(),
            "cooldown_until": None,
        }
        self._pending_events.append(event)
        self._captcha_state = "clear"
        self._captcha_backoff_exp = 0
        self._captcha_seen_at = None
        self._cooldown_until = None
        self._last_error = None

        logger.info(
            {
                "event": "ka_captcha_recovered",
                "platform": self.platform_name,
            }
        )

    def _detect_captcha(self, text_lower: str) -> bool:
        if not text_lower:
            return False
        return any(marker in text_lower for marker in self.CAPTCHA_MARKERS)

    def _parse_search_page(self, html: str) -> _PageParseResult:
        soup = BeautifulSoup(html, "html.parser")
        marker = self._find_organic_marker(soup)
        if not marker:
            return _PageParseResult([], 0, 0)

        listings: List[Listing] = []
        seen: set[str] = set()
        total_items = 0
        promoted_skipped = 0

        for article in marker.find_all_next("article"):
            if not isinstance(article, Tag):
                continue

            if article.get("data-adid") is None and article.get("data-id") is None:
                continue

            total_items += 1

            if self._is_promoted(article):
                promoted_skipped += 1
                continue

            link = article.find("a", href=True)
            if not link:
                continue

            href = link["href"]
            url = urljoin(self.BASE_URL, href)
            platform_id = article.get("data-adid") or article.get("data-id")
            if not platform_id:
                platform_id = self._extract_platform_id(url)

            if not platform_id or not platform_id.isdigit():
                continue

            if platform_id in seen:
                continue
            seen.add(platform_id)

            title = link.get_text(strip=True)

            price_text = None
            price_node = article.find(class_=re.compile("price", re.IGNORECASE))
            if price_node:
                price_text = price_node.get_text(" ", strip=True)

            price_value = None
            price_currency = None
            if price_text:
                match = re.search(r"([0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]{2})?)", price_text)
                if match:
                    try:
                        raw = match.group(1).replace(".", "").replace(",", ".")
                        price_value = float(raw)
                        price_currency = "EUR"
                    except ValueError:
                        price_value = None

            posted_ts = None
            time_tag = article.find("time")
            if time_tag and time_tag.get("datetime"):
                posted_ts = self._parse_datetime_attribute(time_tag["datetime"])

            listing = Listing(
                platform=self.platform_name,
                platform_id=platform_id,
                title=title,
                url=url,
                price_value=price_value,
                price_currency=price_currency,
                price_text=price_text,
                posted_ts=posted_ts,
            )
            listings.append(listing)

        return _PageParseResult(listings, total_items, promoted_skipped)

    def _find_organic_marker(self, soup: BeautifulSoup) -> Optional[Tag]:
        marker = soup.find(lambda tag: tag.name in {"h1", "h2"} and tag.get_text(strip=True).startswith("Alle Artikel"))
        if marker:
            return marker
        return soup.find(attrs={"data-testid": "resultlist"}) or soup.body

    def _is_promoted(self, article: Tag) -> bool:
        classes = article.get("class") or []
        for cls in classes:
            if "topad" in cls or "sponsored" in cls or "highlight" in cls:
                return True

        badge = article.find(lambda tag: isinstance(tag, Tag) and tag.get_text(strip=True).lower() in {"anzeige", "top", "sponsored"})
        return badge is not None

    def _extract_platform_id(self, url: str) -> Optional[str]:
        parsed = urlparse(url)
        path_parts = [part for part in parsed.path.split("/") if part]
        for part in reversed(path_parts):
            match = re.search(r"(\d{5,})", part)
            if match:
                return match.group(1)
        return None

    def _parse_datetime_attribute(self, value: str) -> Optional[datetime]:
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self.berlin_tz)
        return to_utc_aware(dt)

    def _extract_posted_ts_from_detail(self, html: str) -> Optional[datetime]:
        soup = BeautifulSoup(html, "html.parser")
        calendar_icon = soup.find("i", class_=re.compile("calendar", re.IGNORECASE))
        if calendar_icon:
            span = calendar_icon.find_next("span")
            if span:
                parsed = self._parse_posted_ts_text(span.get_text(strip=True))
                if parsed:
                    return parsed

        text_node = soup.find(string=re.compile("Online seit", re.IGNORECASE))
        if text_node:
            parsed = self._parse_posted_ts_text(str(text_node))
            if parsed:
                return parsed

        return None

    def _parse_posted_ts_text(self, text: str) -> Optional[datetime]:
        if not text:
            return None

        cleaned = text.replace("Online seit", "").replace("Uhr", "").strip()
        cleaned = cleaned.replace("·", " ")
        cleaned = re.sub(r"\s+", " ", cleaned)

        lower = cleaned.lower()
        now_berlin = now_utc().astimezone(self.berlin_tz)

        if lower.startswith("heute"):
            time_part = cleaned.split(",", 1)[-1].strip() if "," in cleaned else ""
            if time_part:
                try:
                    hour, minute = [int(x) for x in time_part.split(":", 1)]
                except ValueError:
                    hour = minute = 0
            else:
                hour = minute = 0
            dt = now_berlin.replace(hour=hour, minute=minute, second=0, microsecond=0)
            return to_utc_aware(dt)

        if lower.startswith("gestern"):
            time_part = cleaned.split(",", 1)[-1].strip() if "," in cleaned else ""
            if time_part:
                try:
                    hour, minute = [int(x) for x in time_part.split(":", 1)]
                except ValueError:
                    hour = minute = 0
            else:
                hour = minute = 0
            dt = (now_berlin - timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            return to_utc_aware(dt)

        # Try DD.MM.YYYY HH:MM format
        match = re.search(r"(\d{1,2}\.\d{1,2}\.\d{4})(?:,?\s*(\d{1,2}:\d{2}))?", cleaned)
        if match:
            date_part = match.group(1)
            time_part = match.group(2) or "00:00"
            try:
                dt = datetime.strptime(f"{date_part} {time_part}", "%d.%m.%Y %H:%M")
                dt = dt.replace(tzinfo=self.berlin_tz)
                return to_utc_aware(dt)
            except ValueError:
                pass

        return None

    def _slugify(self, keyword: str) -> str:
        normalized = unicodedata.normalize("NFKC", keyword.strip().lower())
        normalized = re.sub(r"[^\w\s-]", " ", normalized, flags=re.UNICODE)
        normalized = re.sub(r"\s+", "-", normalized.strip())
        return quote(normalized or "suche", safe="-")
