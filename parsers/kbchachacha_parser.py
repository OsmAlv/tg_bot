from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

from parsers.common import _extract_photos, _extract_fuel_type, normalize_display_text, parse_car_from_html
from utils.helpers import CarInfo, fetch_page_html


# Module-level cache: holds the most-recently fetched list.empty HTML so the
# parse fallback can reuse it instead of re-fetching (which might return different results).
_KB_LIST_HTML_CACHE: str | None = None


def prime_kb_list_cache(html: str) -> None:
    """Store list.empty HTML so parse_kbchachacha_listing can reuse it without re-fetching."""
    global _KB_LIST_HTML_CACHE
    _KB_LIST_HTML_CACHE = html


BRAND_KO_TO_EN = {
    "벤츠": "Mercedes-Benz",
    "현대": "Hyundai",
    "기아": "Kia",
    "제네시스": "Genesis",
    "쉐보레": "Chevrolet",
    "아우디": "Audi",
    "BMW": "BMW",
    "폭스바겐": "Volkswagen",
    "포르쉐": "Porsche",
    "렉서스": "Lexus",
    "토요타": "Toyota",
    "닛산": "Nissan",
    "랜드로버": "Land Rover",
    "볼보": "Volvo",
    "테슬라": "Tesla",
}


def _parse_int(value: str | None) -> int | None:
    if not value:
        return None
    digits = re.sub(r"[^0-9]", "", value)
    return int(digits) if digits else None


def _find_value_by_th(soup: BeautifulSoup, label: str) -> str | None:
    th = soup.find("th", string=re.compile(rf"\s*{re.escape(label)}\s*"))
    if not th:
        return None
    td = th.find_next("td")
    if not td:
        return None
    return td.get_text(" ", strip=True)


def _extract_year_and_month_from_text(text: str) -> tuple[int | None, int | None]:
    # 25년11월(26년형) -> production_year_month=202511
    month_match = re.search(r"(\d{2,4})년\s*(\d{1,2})월", text)
    production_year_month: int | None = None
    if month_match:
        yy = int(month_match.group(1))
        mm = int(month_match.group(2))
        year_for_month = yy + 2000 if yy < 100 else yy
        if 1 <= mm <= 12:
            production_year_month = year_for_month * 100 + mm

    # 25년11월(26년형) -> 2026
    m = re.search(r"\((\d{2,4})년형\)", text)
    if m:
        year = int(m.group(1))
        if year < 100:
            year += 2000
        return year, production_year_month

    m = re.search(r"(19\d{2}|20\d{2})년형", text)
    if m:
        return int(m.group(1)), production_year_month

    m = re.search(r"(19\d{2}|20\d{2})", text)
    return (int(m.group(1)) if m else None), production_year_month


def _extract_json_ld_product(soup: BeautifulSoup) -> dict | None:
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        raw = (script.string or script.get_text(" ", strip=True) or "").strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue

        if isinstance(payload, dict) and payload.get("@type") == "Product":
            return payload
    return None


def _extract_engine_cc_fallback(soup: BeautifulSoup) -> int | None:
    # Fallback for pages where displacement is not present in the expected table th/td.
    text = " ".join(soup.stripped_strings)
    script_text = " ".join((script.get_text(" ", strip=True) or "") for script in soup.find_all("script"))
    searchable = f"{text} {script_text}"

    patterns = [
        r"배기량\s*[:：]?\s*([\d,]{3,7})\s*(?:cc|CC)?",
        r"([\d,]{3,7})\s*cc",
        r'"displacement"\s*[:=]\s*"?([\d,]{3,7})"?',
        r'"engineDisplacement"\s*[:=]\s*"?([\d,]{3,7})"?',
    ]
    for pattern in patterns:
        match = re.search(pattern, searchable, flags=re.IGNORECASE)
        if not match:
            continue
        value = _parse_int(match.group(1))
        if value:
            return value

    return None


def _extract_brand_model(product_name: str) -> tuple[str, str]:
    raw = product_name.strip()
    raw = re.sub(r"([A-Za-z0-9]+)-클래스", r"\1-Class", raw)

    brand = ""
    for ko, en in BRAND_KO_TO_EN.items():
        if ko in raw:
            brand = en
            raw = raw.replace(ko, " ").strip()
            break

    cleaned = normalize_display_text(raw)
    cleaned = re.sub(r"\((?:\d{2,4})년형\)", "", cleaned).strip()
    cleaned = re.sub(r"\((?:19|20)\d{2}\s*\)", "", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if not brand:
        # fallback: first token as brand if no known mapping
        parts = cleaned.split(" ", 1)
        if len(parts) == 2:
            return parts[0], parts[1]
        return cleaned or "Unknown", ""

    return brand, cleaned


def _parse_kb_html(html: str, url: str) -> CarInfo:
    soup = BeautifulSoup(html, "html.parser")
    product = _extract_json_ld_product(soup)
    if not product:
        raise ValueError("KB: Product JSON-LD not found")

    name = str(product.get("name") or "")
    if not name:
        title_node = soup.select_one("title")
        name = title_node.get_text(" ", strip=True) if title_node else "Unknown Car"

    brand, model = _extract_brand_model(name)

    offers = product.get("offers") if isinstance(product.get("offers"), dict) else {}
    price_won = _parse_int(str(offers.get("price") or ""))

    year_text = _find_value_by_th(soup, "연식") or name
    mileage_text = _find_value_by_th(soup, "주행거리") or ""
    engine_text = _find_value_by_th(soup, "배기량") or ""
    fuel_text = _find_value_by_th(soup, "연료") or ""

    year, production_year_month = _extract_year_and_month_from_text(year_text)
    mileage_km = _parse_int(mileage_text)
    engine_cc = _parse_int(engine_text)
    if engine_cc is None:
        engine_cc = _extract_engine_cc_fallback(soup)
    fuel_type = _extract_fuel_type(fuel_text) or "Не указано"

    photos: list[str] = []
    images = product.get("image")
    if isinstance(images, list):
        photos.extend(str(x) for x in images if isinstance(x, str))
    photos.extend(_extract_photos(soup, url))
    photos = list(dict.fromkeys(photos))[:10]

    missing = [
        name
        for name, value in {
            "year": year,
            "mileage": mileage_km,
            "engine_cc": engine_cc,
            "price_won": price_won,
        }.items()
        if value is None
    ]
    if missing:
        raise ValueError(f"KB: Missing required fields: {', '.join(missing)}")

    return CarInfo(
        brand=brand or "Unknown",
        model=model,
        year=int(year),
        mileage_km=int(mileage_km),
        engine_cc=int(engine_cc),
        fuel_type=fuel_type,
        price_won=int(price_won),
        photos=photos,
        source_url=url,
        production_year_month=production_year_month,
    )


def _extract_car_seq(url: str) -> str | None:
    match = re.search(r"carSeq=(\d+)", url)
    return match.group(1) if match else None


def _parse_from_kb_list_empty(html: str, url: str) -> CarInfo:
    car_seq = _extract_car_seq(url)
    if not car_seq:
        raise ValueError("KB: carSeq not found in URL")

    soup = BeautifulSoup(html, "html.parser")
    card = soup.select_one(f'div.area[data-car-seq="{car_seq}"]')
    if not card:
        raise ValueError("KB: listing card not found in list.empty")

    title_node = card.select_one("strong.tit")
    title = title_node.get_text(" ", strip=True) if title_node else "Unknown Car"
    brand, model = _extract_brand_model(title)

    info_spans = card.select("div.data-line span")
    year_text = info_spans[0].get_text(" ", strip=True) if len(info_spans) >= 1 else ""
    mileage_text = info_spans[1].get_text(" ", strip=True) if len(info_spans) >= 2 else ""

    year, production_year_month = _extract_year_and_month_from_text(year_text)
    mileage_km = _parse_int(mileage_text)

    price_text = ""
    price_node = card.select_one("span.price") or card.select_one("strong.pay")
    if price_node:
        price_text = price_node.get_text(" ", strip=True)
    price_manwon = _parse_int(price_text)
    price_won = (price_manwon * 10_000) if price_manwon is not None else None

    photos: list[str] = []
    for img in card.select("div.thumnail img"):
        src = img.get("src")
        if src:
            photos.append(src)
    photos = list(dict.fromkeys(photos))[:10]

    missing = [
        name
        for name, value in {
            "year": year,
            "mileage": mileage_km,
            "price_won": price_won,
        }.items()
        if value is None
    ]
    if missing:
        raise ValueError(f"KB list.empty: Missing required fields: {', '.join(missing)}")

    return CarInfo(
        brand=brand or "Unknown",
        model=model,
        year=int(year),
        mileage_km=int(mileage_km),
        engine_cc=1600,
        fuel_type="Не указано",
        price_won=int(price_won),
        photos=photos,
        source_url=url,
        production_year_month=production_year_month,
    )


async def parse_kbchachacha_listing(url: str) -> CarInfo:
    html = await fetch_page_html(url, use_playwright=False)
    try:
        return _parse_kb_html(html, url)
    except Exception:
        # Strict static fallback (no guessed defaults)
        try:
            return parse_car_from_html(html, url, strict=True)
        except Exception:
            # Parse from list endpoint card block by carSeq.
            # Prefer the cached HTML (populated during URL extraction) to avoid
            # a second fetch that might return different/paginated results.
            try:
                list_html = _KB_LIST_HTML_CACHE
                if list_html is None:
                    list_html = await fetch_page_html(
                        "https://www.kbchachacha.com/public/search/list.empty",
                        use_playwright=False,
                    )
                return _parse_from_kb_list_empty(list_html, url)
            except Exception as exc:
                raise ValueError("KB: unable to parse listing from static sources") from exc