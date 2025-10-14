import asyncio
import logging
import os
import random
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from urllib.parse import quote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag
from zoneinfo import ZoneInfo

from models import Listing, SearchResult
from providers.base import BaseProvider

logger = logging.getLogger(__name__)


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

        self.base_delay = float(os.environ.get("KA_BASE_DELAY_SEC", "2.8"))
        self.baseline_delay = float(os.environ.get("KA_BASELINE_DELAY_SEC", "5.0"))
        self.max_retries = int(os.environ.get("KA_MAX_RETRIES", "3"))
        self.backoff_429_min = float(os.environ.get("KA_BACKOFF_429_MIN", "20"))
        self.backoff_403_hours = float(os.environ.get("KA_BACKOFF_403_HOURS", "6"))
        self.cooldown_on_captcha_min = float(os.environ.get("KA_COOLDOWN_ON_CAPTCHA_MIN", "45"))

        self.headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "de-DE,de;q=0.9",
            "Connection": "keep-alive",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }

        self.berlin_tz = ZoneInfo("Europe/Berlin")

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

    @property
    def platform_name(self) -> str:
        return self.PLATFORM

    # ------------------------------------------------------------------
    # Public helpers (used by tests and services)
    # ------------------------------------------------------------------
    def build_search_url(self, keyword: str, page: int = 1) -> str:
        """Construct Kleinanzeigen search URL for keyword/page"""
        slug = self._slugify(keyword)
        if page <= 1:
            path = f"/s-{slug}/k0"
        else:
            path = f"/s-seite:{page}/{slug}/k0"
        return urljoin(self.BASE_URL, path)

    def _candidate_paths(self, keyword: str, page: int) -> List[str]:
        slug = self._slugify(keyword) or "suche"
        candidates: List[str] = []

        if page <= 1:
            raw = [
                f"/s-{slug}/k0",
                f"/s-suchanfrage/{slug}/k0",
                f"/s/{slug}/k0",
            ]
        else:
            raw = [
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
    async def _warmup_session(self, client: httpx.AsyncClient, mode: str) -> None:
        if self._session_warmed:
            return

        try:
            delay = self.baseline_delay if mode == "baseline" else self.base_delay
            await self._respect_rate_limit(delay)
            response = await client.get(self.BASE_URL + "/")
            client.cookies.update(response.cookies)
            response.raise_for_status()
            self._session_warmed = True
            logger.info(
                {
                    "event": "ka_warmup",
                    "platform": self.platform_name,
                    "status": response.status_code,
                    "final_url": str(response.url),
                }
            )
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

    @staticmethod
    def _is_consent_page(final_url: str, text_lower: str) -> bool:
        if not final_url:
            return False
        final_lower = final_url.lower()
        if "consent" in final_lower or "datenschutz" in final_lower:
            return True
        return "zustimmen" in text_lower or "cookie" in text_lower

    @staticmethod
    def _apply_consent_cookies(client: httpx.AsyncClient, response: httpx.Response) -> None:
        client.cookies.update(response.cookies)
        consent_cookies = {
            "cookieBanner": "1",
            "cookie_consent": "1",
            "topbox": "accepted",
        }
        for name, value in consent_cookies.items():
            client.cookies.set(name, value, domain="www.kleinanzeigen.de")

    async def search(
        self,
        keyword: str,
        since_ts: Optional[datetime] = None,
        sample_mode: bool = False,
        crawl_all: bool = False,
        max_pages_override: Optional[int] = None,
    ) -> SearchResult:
        mode = "baseline" if crawl_all else "poll"

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
            self._session_warmed = False
            await self._warmup_session(client, mode)

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
                page_items, items_total, promoted_skipped = self._parse_search_page(html)
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
            and datetime.now(timezone.utc) < self._cooldown_until,
        }
        return data

    def _drain_events(self) -> List[dict]:
        events = list(self._pending_events)
        self._pending_events.clear()
        return events

    def _can_attempt_request(self) -> bool:
        if self._cooldown_until is None:
            return True
        return datetime.now(timezone.utc) >= self._cooldown_until

    async def _respect_rate_limit(self, delay: float) -> None:
        delay = max(0.5, delay)
        async with self._rate_lock:
            now = datetime.now(timezone.utc)
            if self._last_request_at is not None:
                elapsed = (now - self._last_request_at).total_seconds()
                wait_for = delay + random.uniform(0.2, 0.8) - elapsed
                if wait_for > 0:
                    await asyncio.sleep(wait_for)
            self._last_request_at = datetime.now(timezone.utc)

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
                response = await client.get(url)
            except httpx.HTTPError as exc:
                last_exc = exc
                await asyncio.sleep(min(5, 1 + attempt))
                continue

            final_url = str(response.url)
            status = response.status_code
            text_lower = response.text.lower() if response.text else ""

            if status in (403, 429, 503):
                self._handle_block(
                    status,
                    keyword=keyword,
                    page=page,
                    url=url,
                    final_url=final_url,
                )
                return None, status, final_url, "block"

            if self._is_consent_page(final_url, text_lower):
                if not consent_retry:
                    self._apply_consent_cookies(client, response)
                    consent_retry = True
                    continue
                self._last_error = "Consent-Bestätigung erforderlich"
                logger.warning(
                    {
                        "event": "ka_consent_block",
                        "platform": self.platform_name,
                        "q": keyword,
                        "page": page,
                        "url": final_url,
                    }
                )
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
        now = datetime.now(timezone.utc)
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
        now = datetime.now(timezone.utc)
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

        logger.warning(
            {
                "event": "ka_recaptcha",
                "platform": self.platform_name,
                "q": keyword,
                "page": page,
                "url": final_url or url,
                "status": status,
            }
        )

    def _handle_captcha_recovered(self) -> None:
        if self._captcha_state != "entered":
            return

        now = datetime.now(timezone.utc)
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

    def _parse_search_page(self, html: str) -> tuple[List[Listing], int, int]:
        soup = BeautifulSoup(html, "html.parser")
        marker = self._find_organic_marker(soup)
        if not marker:
            return [], 0, 0

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

        return listings, total_items, promoted_skipped

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
        return dt.astimezone(timezone.utc)

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
        now_berlin = datetime.now(self.berlin_tz)

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
            return dt.astimezone(timezone.utc)

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
            return dt.astimezone(timezone.utc)

        # Try DD.MM.YYYY HH:MM format
        match = re.search(r"(\d{1,2}\.\d{1,2}\.\d{4})(?:,?\s*(\d{1,2}:\d{2}))?", cleaned)
        if match:
            date_part = match.group(1)
            time_part = match.group(2) or "00:00"
            try:
                dt = datetime.strptime(f"{date_part} {time_part}", "%d.%m.%Y %H:%M")
                dt = dt.replace(tzinfo=self.berlin_tz)
                return dt.astimezone(timezone.utc)
            except ValueError:
                pass

        return None

    def _slugify(self, keyword: str) -> str:
        normalized = unicodedata.normalize("NFKC", keyword.strip().lower())
        normalized = re.sub(r"[^\w\s-]", " ", normalized, flags=re.UNICODE)
        normalized = re.sub(r"\s+", "-", normalized.strip())
        return quote(normalized or "suche", safe="-")
