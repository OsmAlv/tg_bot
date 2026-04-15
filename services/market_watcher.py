from __future__ import annotations

import asyncio
import csv
import html as html_lib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlencode, urlparse

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from bs4 import BeautifulSoup

from bot.main import _send_result, build_car_message
from parsers import detect_marketplace, parse_listing
from parsers.kbchachacha_parser import prime_kb_list_cache
from services.currency_service import CurrencyService
from services.price_calculator import PriceCalculator
from utils.helpers import fetch_page_html

logger = logging.getLogger(__name__)


@dataclass
class WatchFilters:
    brand_contains: list[str] = field(default_factory=list)
    model_contains: list[str] = field(default_factory=list)
    fuel_types: list[str] = field(default_factory=list)
    year_min: int | None = None
    year_max: int | None = None
    year_month_min: int | None = None
    year_month_max: int | None = None
    mileage_max: int | None = None
    engine_cc_min: int | None = None
    engine_cc_max: int | None = None
    price_usd_min: float | None = None
    price_usd_max: float | None = None
    final_price_usd_min: float | None = None
    final_price_usd_max: float | None = None


@dataclass
class WatchPreset:
    name: str
    search_urls: list[str]
    max_candidates_per_url: int = 30
    max_posts_per_run: int = 3
    filters: WatchFilters = field(default_factory=WatchFilters)


@dataclass
class WatchResult:
    checked: int = 0
    matched: int = 0
    posted: int = 0


@dataclass
class CandidateListing:
    listing_url: str
    source_search_url: str


@dataclass
class WatchTableRow:
    timestamp_utc: str
    preset: str
    source_search_url: str
    listing_url: str
    seen_key: str
    marketplace: str
    status: str
    reason: str
    telegram_post_url: str = ""
    brand: str = ""
    model: str = ""
    year: str = ""
    production_year_month: str = ""
    mileage_km: str = ""
    engine_cc: str = ""
    fuel_type: str = ""
    price_korea_usd: str = ""
    final_price_usd: str = ""


def load_watch_presets(config_path: str | Path) -> list[WatchPreset]:
    path = Path(config_path)
    if not path.exists():
        raise ValueError(f"Auto-scan config not found: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("Auto-scan config must be a non-empty JSON array")

    presets: list[WatchPreset] = []
    for item in raw:
        if not isinstance(item, dict):
            continue

        filters_raw = item.get("filters") if isinstance(item.get("filters"), dict) else {}
        filters = WatchFilters(
            brand_contains=_as_str_list(filters_raw.get("brand_contains")),
            model_contains=_as_str_list(filters_raw.get("model_contains")),
            fuel_types=_as_str_list(filters_raw.get("fuel_types")),
            year_min=_as_int(filters_raw.get("year_min")),
            year_max=_as_int(filters_raw.get("year_max")),
            year_month_min=_as_int(filters_raw.get("year_month_min")),
            year_month_max=_as_int(filters_raw.get("year_month_max")),
            mileage_max=_as_int(filters_raw.get("mileage_max")),
            engine_cc_min=_as_int(filters_raw.get("engine_cc_min")),
            engine_cc_max=_as_int(filters_raw.get("engine_cc_max")),
            price_usd_min=_as_float(filters_raw.get("price_usd_min")),
            price_usd_max=_as_float(filters_raw.get("price_usd_max")),
            final_price_usd_min=_as_float(filters_raw.get("final_price_usd_min")),
            final_price_usd_max=_as_float(filters_raw.get("final_price_usd_max")),
        )

        preset = WatchPreset(
            name=str(item.get("name") or f"preset_{len(presets) + 1}"),
            search_urls=_as_str_list(item.get("search_urls")),
            max_candidates_per_url=_as_int(item.get("max_candidates_per_url")) or 30,
            max_posts_per_run=_as_int(item.get("max_posts_per_run")) or 3,
            filters=filters,
        )
        if preset.search_urls:
            presets.append(preset)

    if not presets:
        raise ValueError("No valid presets in auto-scan config")
    return presets


def load_seen_urls(state_path: str | Path) -> set[str]:
    path = Path(state_path)
    if not path.exists():
        return set()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return {str(x) for x in payload if isinstance(x, str)}
        if isinstance(payload, dict) and isinstance(payload.get("seen"), list):
            return {str(x) for x in payload["seen"] if isinstance(x, str)}
    except Exception:
        logger.warning("Failed to load seen URLs state file", exc_info=True)

    return set()


def save_seen_urls(state_path: str | Path, seen_urls: set[str]) -> None:
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "seen": sorted(seen_urls),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _build_telegram_post_url(channel_id: str | int, message_id: int) -> str:
    channel_text = str(channel_id).strip()
    if channel_text.startswith("@"):
        return f"https://t.me/{channel_text[1:]}/{message_id}"

    # Fallback for numeric channel IDs: -100xxxxxxxxxx -> t.me/c/xxxxxxxxxx/<message_id>
    if channel_text.startswith("-100") and channel_text[4:].isdigit():
        return f"https://t.me/c/{channel_text[4:]}/{message_id}"
    if channel_text.startswith("-") and channel_text[1:].isdigit():
        return f"https://t.me/c/{channel_text[1:]}/{message_id}"

    return ""


def save_watch_results_table(results_path: str | Path, rows: list[WatchTableRow]) -> None:
    path = Path(results_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "timestamp_utc",
        "preset",
        "source_search_url",
        "listing_url",
        "seen_key",
        "marketplace",
        "status",
        "reason",
        "telegram_post_url",
        "brand",
        "model",
        "year",
        "production_year_month",
        "mileage_km",
        "engine_cc",
        "fuel_type",
        "price_korea_usd",
        "final_price_usd",
    ]

    file_exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "timestamp_utc": row.timestamp_utc,
                    "preset": row.preset,
                    "source_search_url": row.source_search_url,
                    "listing_url": row.listing_url,
                    "seen_key": row.seen_key,
                    "marketplace": row.marketplace,
                    "status": row.status,
                    "reason": row.reason,
                    "telegram_post_url": row.telegram_post_url,
                    "brand": row.brand,
                    "model": row.model,
                    "year": row.year,
                    "production_year_month": row.production_year_month,
                    "mileage_km": row.mileage_km,
                    "engine_cc": row.engine_cc,
                    "fuel_type": row.fuel_type,
                    "price_korea_usd": row.price_korea_usd,
                    "final_price_usd": row.final_price_usd,
                }
            )


async def run_market_watch(
    bot: Bot,
    channel_id: str,
    manager_chat_url: str,
    currency_service: CurrencyService,
    price_calculator: PriceCalculator,
    presets: list[WatchPreset],
    seen_urls: set[str],
) -> WatchResult:
    result = WatchResult()
    table_rows: list[WatchTableRow] = []
    results_path = os.getenv("AUTO_SCAN_RESULTS_PATH", "data/autopost_results.csv")
    processed_seen_keys: set[str] = set()

    manager_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="💬 Написать менеджеру", url=manager_chat_url)]]
    )

    for preset in presets:
        posted_for_preset = 0
        logger.info("Scanning preset: %s", preset.name)

        candidate_urls = await _collect_candidate_listing_urls(
            preset.search_urls,
            max_candidates_per_url=preset.max_candidates_per_url,
        )

        for candidate in candidate_urls:
            listing_url = candidate.listing_url
            seen_key = _listing_seen_key(listing_url)
            if seen_key in processed_seen_keys:
                table_rows.append(
                    WatchTableRow(
                        timestamp_utc=_utc_now_iso(),
                        preset=preset.name,
                        source_search_url=candidate.source_search_url,
                        listing_url=listing_url,
                        seen_key=seen_key,
                        marketplace="",
                        status="skipped_duplicate_in_run",
                        reason="same listing key already processed in this run",
                    )
                )
                continue

            if listing_url in seen_urls or seen_key in seen_urls:
                table_rows.append(
                    WatchTableRow(
                        timestamp_utc=_utc_now_iso(),
                        preset=preset.name,
                        source_search_url=candidate.source_search_url,
                        listing_url=listing_url,
                        seen_key=seen_key,
                        marketplace="",
                        status="skipped_seen",
                        reason="already in seen state",
                    )
                )
                continue

            processed_seen_keys.add(seen_key)
            if posted_for_preset >= preset.max_posts_per_run:
                break

            marketplace = detect_marketplace(listing_url)
            if marketplace is None:
                table_rows.append(
                    WatchTableRow(
                        timestamp_utc=_utc_now_iso(),
                        preset=preset.name,
                        source_search_url=candidate.source_search_url,
                        listing_url=listing_url,
                        seen_key=seen_key,
                        marketplace="",
                        status="skipped_unknown_marketplace",
                        reason="marketplace detection failed",
                    )
                )
                continue

            result.checked += 1

            try:
                car = await parse_listing(listing_url, marketplace)
                if car.price_won <= 0 or car.mileage_km <= 0:
                    logger.info(
                        "Skip listing due to missing critical fields (price/mileage): %s",
                        listing_url,
                    )
                    table_rows.append(
                        WatchTableRow(
                            timestamp_utc=_utc_now_iso(),
                            preset=preset.name,
                            source_search_url=candidate.source_search_url,
                            listing_url=listing_url,
                            seen_key=seen_key,
                            marketplace=marketplace.value,
                            status="skipped_incomplete",
                            reason="missing critical fields (price/mileage)",
                            brand=str(getattr(car, "brand", "") or ""),
                            model=str(getattr(car, "model", "") or ""),
                            year=str(getattr(car, "year", "") or ""),
                            production_year_month=str(getattr(car, "production_year_month", "") or ""),
                            mileage_km=str(getattr(car, "mileage_km", "") or ""),
                            engine_cc=str(getattr(car, "engine_cc", "") or ""),
                            fuel_type=str(getattr(car, "fuel_type", "") or ""),
                        )
                    )
                    continue

                price_korea_usd = await currency_service.source_to_usd(car.price_won, car.price_currency)
                logger.info(
                    "Parsed: %s %s | year=%s ym=%s | %.0f km | $%.0f | %s",
                    car.brand,
                    car.model,
                    car.year,
                    getattr(car, "production_year_month", None),
                    car.mileage_km,
                    price_korea_usd,
                    listing_url,
                )
                usd_uzs = await currency_service.usd_to_uzs_rate()
                price_result = price_calculator.calculate(
                    car_price_usd=price_korea_usd,
                    car_year=car.year,
                    engine_cc=car.engine_cc,
                    usd_uzs=usd_uzs,
                    fuel_type=car.fuel_type,
                )

                if not _matches_filters(
                    car=car,
                    price_korea_usd=price_korea_usd,
                    final_price_usd=price_result.final_price_usd,
                    filters=preset.filters,
                ):
                    table_rows.append(
                        WatchTableRow(
                            timestamp_utc=_utc_now_iso(),
                            preset=preset.name,
                            source_search_url=candidate.source_search_url,
                            listing_url=listing_url,
                            seen_key=seen_key,
                            marketplace=marketplace.value,
                            status="filtered_out",
                            reason="does not match preset filters",
                            brand=car.brand,
                            model=car.model,
                            year=str(car.year),
                            production_year_month=str(getattr(car, "production_year_month", "") or ""),
                            mileage_km=str(car.mileage_km),
                            engine_cc=str(car.engine_cc),
                            fuel_type=car.fuel_type,
                            price_korea_usd=f"{price_korea_usd:.2f}",
                            final_price_usd=f"{price_result.final_price_usd:.2f}",
                        )
                    )
                    continue

                result.matched += 1
                text = build_car_message(
                    brand=car.brand,
                    model=car.model,
                    year=car.year,
                    mileage_km=car.mileage_km,
                    engine_cc=car.engine_cc,
                    fuel_type=car.fuel_type,
                    price_korea_usd=price_korea_usd,
                    final_price_usd=price_result.final_price_usd,
                    is_approximate=marketplace.value == "generic",
                )

                sent_message = await _send_result(bot, channel_id, text, car.photos, reply_markup=manager_keyboard)
                telegram_post_url = _build_telegram_post_url(channel_id, sent_message.message_id)
                # Save stable dedupe key + original URL (backward compatibility)
                seen_urls.add(seen_key)
                seen_urls.add(listing_url)
                table_rows.append(
                    WatchTableRow(
                        timestamp_utc=_utc_now_iso(),
                        preset=preset.name,
                        source_search_url=candidate.source_search_url,
                        listing_url=listing_url,
                        seen_key=seen_key,
                        marketplace=marketplace.value,
                        status="posted",
                        reason="matched and published",
                        telegram_post_url=telegram_post_url,
                        brand=car.brand,
                        model=car.model,
                        year=str(car.year),
                        production_year_month=str(getattr(car, "production_year_month", "") or ""),
                        mileage_km=str(car.mileage_km),
                        engine_cc=str(car.engine_cc),
                        fuel_type=car.fuel_type,
                        price_korea_usd=f"{price_korea_usd:.2f}",
                        final_price_usd=f"{price_result.final_price_usd:.2f}",
                    )
                )
                posted_for_preset += 1
                result.posted += 1
                await asyncio.sleep(2)
            except ValueError as exc:
                if "Missing required fields" in str(exc):
                    logger.info("Skip listing with incomplete data: %s", listing_url)
                    table_rows.append(
                        WatchTableRow(
                            timestamp_utc=_utc_now_iso(),
                            preset=preset.name,
                            source_search_url=candidate.source_search_url,
                            listing_url=listing_url,
                            seen_key=seen_key,
                            marketplace=marketplace.value,
                            status="skipped_incomplete",
                            reason=str(exc),
                        )
                    )
                else:
                    logger.warning("Failed to process listing: %s (%s)", listing_url, exc)
                    table_rows.append(
                        WatchTableRow(
                            timestamp_utc=_utc_now_iso(),
                            preset=preset.name,
                            source_search_url=candidate.source_search_url,
                            listing_url=listing_url,
                            seen_key=seen_key,
                            marketplace=marketplace.value,
                            status="failed",
                            reason=str(exc),
                        )
                    )
            except Exception as exc:
                logger.warning("Failed to process listing: %s (%s)", listing_url, exc)
                table_rows.append(
                    WatchTableRow(
                        timestamp_utc=_utc_now_iso(),
                        preset=preset.name,
                        source_search_url=candidate.source_search_url,
                        listing_url=listing_url,
                        seen_key=seen_key,
                        marketplace=marketplace.value,
                        status="failed",
                        reason=str(exc),
                    )
                )

    if table_rows:
        save_watch_results_table(results_path, table_rows)
        logger.info("Saved %d scan rows to %s", len(table_rows), results_path)

    return result


async def _collect_candidate_listing_urls(search_urls: list[str], max_candidates_per_url: int) -> list[CandidateListing]:
    # Collect per-source URL lists first, then mix them in round-robin order.
    # This prevents one marketplace (e.g., Encar) from starving others (KB/KCar)
    # when runs are time-limited.
    per_source_urls: list[list[CandidateListing]] = []
    global_seen: set[str] = set()
    global_seen_keys: set[str] = set()

    for search_url in search_urls:
        urls = await _extract_listing_urls_from_page(search_url, max_count=max_candidates_per_url)
        filtered: list[CandidateListing] = []
        for url in urls:
            seen_key = _listing_seen_key(url)
            if url in global_seen or seen_key in global_seen_keys:
                continue
            global_seen.add(url)
            global_seen_keys.add(seen_key)
            filtered.append(CandidateListing(listing_url=url, source_search_url=search_url))
            if len(filtered) >= max_candidates_per_url:
                break
        per_source_urls.append(filtered)

    candidates: list[CandidateListing] = []
    global_limit = max_candidates_per_url * max(1, len(search_urls))
    index = 0

    while len(candidates) < global_limit:
        progressed = False
        for source_urls in per_source_urls:
            if index < len(source_urls):
                candidates.append(source_urls[index])
                progressed = True
                if len(candidates) >= global_limit:
                    break
        if not progressed:
            break
        index += 1

    return candidates


async def _extract_listing_urls_from_page(url: str, max_count: int = 120) -> list[str]:
    # Allow direct listing URLs in presets
    marketplace = detect_marketplace(url)
    if marketplace is not None and _looks_like_listing_url(url):
        return [url]

    disable_playwright = os.getenv("AUTO_SCAN_DISABLE_PLAYWRIGHT", "1").strip().lower() in {"1", "true", "yes"}

    lower_url = url.lower()
    if "encar.com/fc/fc_carsearchlist.do" in lower_url and disable_playwright:
        logger.info("Skip Encar search extraction in static-only mode (requires Playwright): %s", url)
        return []

    if "kbchachacha.com/public/search/main.kbc" in lower_url:
        # Extract hash-fragment params, e.g. #!?page=1&regiDay=2025 → {regiDay: 2025}
        fragment = urlparse(url).fragment  # e.g. "!?page=1&regiDay=2025"
        base_kb_params: dict[str, str] = {}
        if "?" in fragment:
            for pair in fragment.split("?", 1)[1].split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    if k != "page":  # page is controlled by the pagination loop
                        base_kb_params[k] = v

        all_kb_urls: list[str] = []
        all_kb_html_parts: list[str] = []
        seen_kb: set[str] = set()

        for page_num in range(1, 4):  # up to 3 pages (~120 listings)
            page_params = {**base_kb_params, "page": str(page_num)}
            kb_list_url = "https://www.kbchachacha.com/public/search/list.empty?" + urlencode(page_params)
            try:
                html = await fetch_page_html(kb_list_url, use_playwright=False)
                page_urls = _extract_listing_urls_from_html(html, kb_list_url)
                new_urls = [u for u in page_urls if u not in seen_kb]
                if not new_urls:
                    break  # No new listings on this page — stop paginating
                for u in new_urls:
                    seen_kb.add(u)
                    all_kb_urls.append(u)
                all_kb_html_parts.append(html)
                if len(all_kb_urls) >= max_count:
                    break
            except Exception:
                logger.warning("KB list.empty page %d fetch failed", page_num)
                break

        # Cache combined HTML for use during per-listing parse fallback
        if all_kb_html_parts:
            prime_kb_list_cache("\n".join(all_kb_html_parts))

        logger.info(
            "Extracted %d candidate listing URLs from KB list endpoint (%d page(s) fetched)",
            len(all_kb_urls),
            len(all_kb_html_parts),
        )
        return all_kb_urls

    html = await fetch_page_html(url, use_playwright=False)
    deduped = _extract_listing_urls_from_html(html, url)

    # Search pages are often JS-rendered; if static HTML gives too few links, retry with Playwright.
    is_search_like = any(token in url.lower() for token in ("search", "carsearchlist", "main.kbc", "#!"))
    if is_search_like and len(deduped) < 5 and not disable_playwright:
        try:
            rendered_html = await fetch_page_html(url, use_playwright=True)
            rendered_urls = _extract_listing_urls_from_html(rendered_html, url)
            if len(rendered_urls) > len(deduped):
                deduped = rendered_urls
        except Exception:
            logger.warning("Playwright extraction failed for search page: %s", url)
    elif is_search_like and len(deduped) < 5 and disable_playwright:
        logger.info("Playwright disabled for auto-scan; using static extraction only for %s", url)

    logger.info("Extracted %d candidate listing URLs from %s", len(deduped), url)
    return deduped


def _extract_listing_urls_from_html(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    import re

    raw_urls: list[str] = []
    for anchor in soup.select("a[href]"):
        href = anchor.get("href")
        if not href:
            continue
        absolute = urljoin(base_url, href)
        if not absolute.startswith(("http://", "https://")):
            continue
        if detect_marketplace(absolute) is None:
            continue
        if not _looks_like_listing_url(absolute):
            continue
        raw_urls.append(absolute)

    # Regex fallback for hidden JS links
    patterns = [
        r"https?://(?:www\.)?encar\.com/dc/dc_cardetailview\.do\?[^\s\"'<>]+",
        r"https?://(?:www\.)?kbchachacha\.com/public/car/detail[^\s\"'<>]*",
        r"https?://(?:www\.)?kcar\.com/bc/detail/car/[^\s\"'<>]*",
    ]
    for pattern in patterns:
        raw_urls.extend(re.findall(pattern, html, flags=re.IGNORECASE))

    # 2) Relative URLs
    rel_patterns = [
        r"/dc/dc_cardetailview\.do\?[^\s\"'<>]+",
        r"/public/car/detail[^\s\"'<>]*",
        r"/bc/detail/car/[^\s\"'<>]*",
    ]
    for pattern in rel_patterns:
        for rel in re.findall(pattern, html, flags=re.IGNORECASE):
            raw_urls.append(urljoin(base_url, rel))

    # 3) Encar rendered rows often contain data-impression="<carid>|..."
    for car_id in re.findall(r'data-impression="(\d+)\|', html):
        raw_urls.append(f"https://www.encar.com/dc/dc_cardetailview.do?carid={car_id}")

    deduped: list[str] = []
    seen: set[str] = set()
    for item in raw_urls:
        clean = html_lib.unescape(item).split("#", 1)[0]
        clean = _normalize_listing_url(clean)
        if not _looks_like_listing_url(clean):
            continue
        if clean in seen:
            continue
        seen.add(clean)
        deduped.append(clean)

    return deduped


def _normalize_listing_url(url: str) -> str:
    # Keep original listing URL intact. Some marketplaces rely on additional
    # query params/context and may not work with canonicalized links.
    return url


def _listing_seen_key(url: str) -> str:
    normalized_url = html_lib.unescape(url)
    parsed = urlparse(normalized_url)
    lower = normalized_url.lower()

    if "encar.com" in lower:
        query = parse_qs(parsed.query)
        carid = (query.get("carid") or [None])[0]
        if carid and str(carid).isdigit():
            return f"encar:{carid}"
        match = re.search(r"(?:[?&])carid=(\d+)", normalized_url)
        if match:
            return f"encar:{match.group(1)}"
        match = re.search(r"/cars/detail/(\d+)", parsed.path)
        if match:
            return f"encar:{match.group(1)}"

    if "kbchachacha.com" in lower:
        query = parse_qs(parsed.query)
        car_seq = (query.get("carSeq") or [None])[0]
        if car_seq and str(car_seq).isdigit():
            return f"kb:{car_seq}"
        match = re.search(r"(?:[?&])carSeq=(\d+)", normalized_url)
        if match:
            return f"kb:{match.group(1)}"
        match = re.search(r"/public/car/detail[^\d]*(\d+)", parsed.path)
        if match:
            return f"kb:{match.group(1)}"

    if "kcar.com" in lower:
        match = re.search(r"/bc/detail/car/(\d+)", parsed.path)
        if match:
            return f"kcar:{match.group(1)}"

    # Fallback for unknown patterns
    return normalized_url.split("#", 1)[0]


def _looks_like_listing_url(url: str) -> bool:
    lower = url.lower()

    if "encar.com" in lower:
        return "dc_cardetailview.do" in lower and "carid=" in lower
    if "kbchachacha.com" in lower:
        return "/public/car/detail" in lower
    if "kcar.com" in lower:
        return "/bc/detail/car/" in lower

    return False


def _matches_filters(car: Any, price_korea_usd: float, final_price_usd: float, filters: WatchFilters) -> bool:
    brand = str(getattr(car, "brand", "") or "").lower()
    model = str(getattr(car, "model", "") or "").lower()
    fuel = str(getattr(car, "fuel_type", "") or "").lower()
    year = int(getattr(car, "year", 0) or 0)
    production_year_month = getattr(car, "production_year_month", None)
    production_year_month = int(production_year_month) if production_year_month else None
    mileage = int(getattr(car, "mileage_km", 0) or 0)
    engine_cc = int(getattr(car, "engine_cc", 0) or 0)

    if filters.brand_contains and not any(x.lower() in brand for x in filters.brand_contains):
        return False
    if filters.model_contains and not any(x.lower() in model for x in filters.model_contains):
        return False
    if filters.fuel_types and not any(x.lower() in fuel for x in filters.fuel_types):
        return False

    if filters.year_min is not None and year < filters.year_min:
        return False
    if filters.year_max is not None and year > filters.year_max:
        return False

    if filters.year_month_min is not None:
        if production_year_month is not None:
            if production_year_month < filters.year_month_min:
                return False
        else:
            # If production month is unknown, only reject when the car year is strictly
            # below the threshold year (e.g. threshold=202511 → threshold_year=2025,
            # so a 2025 car without month info is allowed through; a 2024 car is not).
            threshold_year = filters.year_month_min // 100
            if year < threshold_year:
                return False

    if filters.year_month_max is not None:
        if production_year_month is not None:
            if production_year_month > filters.year_month_max:
                return False
        else:
            threshold_year = filters.year_month_max // 100
            if year > threshold_year:
                return False

    if filters.mileage_max is not None and mileage > filters.mileage_max:
        return False
    if filters.engine_cc_min is not None and engine_cc < filters.engine_cc_min:
        return False
    if filters.engine_cc_max is not None and engine_cc > filters.engine_cc_max:
        return False

    if filters.price_usd_min is not None and price_korea_usd < filters.price_usd_min:
        return False
    if filters.price_usd_max is not None and price_korea_usd > filters.price_usd_max:
        return False

    if filters.final_price_usd_min is not None and final_price_usd < filters.final_price_usd_min:
        return False
    if filters.final_price_usd_max is not None and final_price_usd > filters.final_price_usd_max:
        return False

    return True


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return []


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
