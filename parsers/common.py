from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from utils.helpers import CarInfo, fetch_page_html


FUEL_MAP = {
    "gasoline": "Бензин",
    "petrol": "Бензин",
    "бензин": "Бензин",
    "휘발유": "Бензин",
    "가솔린": "Бензин",
    "diesel": "Дизель",
    "дизель": "Дизель",
    "경유": "Дизель",
    "디젤": "Дизель",
    "lpg": "Газ",
    "газ": "Газ",
    "엘피지": "Газ",
    "전기": "Электро",
    "electric": "Электро",
    "электро": "Электро",
    "электр": "Электро",
    "hybrid": "Гибрид",
    "гибрид": "Гибрид",
    "하이브리드": "Гибрид",
}


def normalize_display_text(value: str) -> str:
    text = (value or "").strip()
    # Убираем корейские символы (Hangul) из отображаемых названий
    text = re.sub(r"[\u1100-\u11FF\u3130-\u318F\uAC00-\uD7AF]+", " ", text)
    # Нормализуем разделители и пробелы
    text = re.sub(r"\s*[-–—|/]+\s*", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -|/")
    return text


def _extract_text(soup: BeautifulSoup) -> str:
    return " ".join(soup.stripped_strings)


def _extract_script_text(soup: BeautifulSoup) -> str:
    chunks: list[str] = []
    for script in soup.find_all("script"):
        content = script.string or script.get_text(" ", strip=True)
        if not content:
            continue

        chunks.append(content)

        if script.get("type") == "application/ld+json":
            try:
                payload = json.loads(content)
                chunks.append(json.dumps(payload, ensure_ascii=False))
            except Exception:
                continue

    return " ".join(chunks)


def _iter_json_ld_payloads(soup: BeautifulSoup) -> list[dict]:
    payloads: list[dict] = []
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        raw = (script.string or script.get_text(" ", strip=True) or "").strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue

        if isinstance(payload, list):
            payloads.extend(item for item in payload if isinstance(item, dict))
        elif isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _extract_title(soup: BeautifulSoup) -> str:
    og_title = soup.select_one("meta[property='og:title']")
    if og_title and og_title.get("content"):
        return og_title["content"].strip()

    for selector in ["h1", "title", ".title", "[class*='title']"]:
        node = soup.select_one(selector)
        if node:
            text = node.get_text(" ", strip=True)
            if text:
                return text

    return "Unknown Car"


def _extract_brand_model(title: str) -> tuple[str, str]:
    cleaned = normalize_display_text(re.sub(r"\s+", " ", title).strip())
    parts = cleaned.split(" ")
    if len(parts) == 1:
        return parts[0], ""
    if len(parts) == 2:
        return parts[0], parts[1]
    return parts[0], " ".join(parts[1:])


def _parse_int(value: str) -> int | None:
    digits = re.sub(r"[^0-9]", "", value)
    return int(digits) if digits else None


def _extract_year(text: str) -> int | None:
    match = re.search(r"\b(19\d{2}|20\d{2})\b", text)
    return int(match.group(1)) if match else None


def _extract_mileage(text: str) -> int | None:
    patterns = [
        r"([\d\s,\.]+)\s*(?:km|KM|км|킬로|주행)",
        r"주행거리\s*[:：]?\s*([\d,\.]+)",
        r"пробег\s*[:：]?\s*([\d\s,\.]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = _parse_int(match.group(1))
            if value:
                return value
    return None


def _extract_engine_cc(text: str) -> int | None:
    patterns = [
        r"([\d,\.]{3,7})\s*cc",
        r"배기량\s*[:：]?\s*([\d,\.]{3,7})",
        r"displacement\s*[:=]\s*['\"]?([\d,\.]{3,7})",
        r"двигател(?:ь|я)\s*[:：]?\s*([1-9](?:[\.,]\d)?)\s*l",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            if "l" in pattern and re.search(r"[\.,]", match.group(1)):
                liters = float(match.group(1).replace(",", "."))
                return int(liters * 1000)
            value = _parse_int(match.group(1))
            if value:
                return value

    liter_match = re.search(r"([1-9](?:[\.,]\d)?)\s*(?:l|литр|литра|리터)", text, flags=re.IGNORECASE)
    if liter_match:
        liters = float(liter_match.group(1))
        return int(liters * 1000)

    return None


def _extract_fuel_type(text: str) -> str | None:
    lower = text.lower()
    for key, value in FUEL_MAP.items():
        if key in lower:
            return value
    return None


def _extract_price_won(soup: BeautifulSoup, text: str) -> int | None:
    price_meta = soup.select_one("meta[property='product:price:amount']")
    if price_meta and price_meta.get("content"):
        value = _parse_int(price_meta["content"])
        if value:
            return value

    patterns = [
        r"([\d,]{4,})\s*(?:원|krw|won)",
        r"가격\s*[:：]?\s*([\d,]{4,})",
        r"([\d,]{3,})\s*만원",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            raw = _parse_int(match.group(1))
            if raw:
                if "만원" in pattern:
                    return raw * 10_000
                return raw
    return None


def _extract_price_and_currency(soup: BeautifulSoup, text: str) -> tuple[int | None, str]:
    price_meta = soup.select_one("meta[property='product:price:amount']")
    currency_meta = soup.select_one("meta[property='product:price:currency']")
    if price_meta and price_meta.get("content"):
        value = _parse_int(price_meta["content"])
        if value:
            currency = (currency_meta.get("content") if currency_meta else "KRW") or "KRW"
            return value, currency.upper()

    for payload in _iter_json_ld_payloads(soup):
        offers = payload.get("offers")
        if isinstance(offers, list):
            offers = next((item for item in offers if isinstance(item, dict)), None)
        if not isinstance(offers, dict):
            offers = payload if payload.get("price") else None
        if not isinstance(offers, dict):
            continue

        price_value = offers.get("price")
        currency_value = offers.get("priceCurrency") or payload.get("priceCurrency") or "KRW"
        if price_value is None:
            continue
        value = _parse_int(str(price_value))
        if value:
            return value, str(currency_value).upper()

    currency_patterns = [
        (r"₽\s*([\d\s,]+)", "RUB"),
        (r"([\d\s,]+)\s*₽", "RUB"),
        (r"\b([\d\s,]+)\s*(?:руб|рублей|рублей|руб\.)", "RUB"),
        (r"$\s*([\d\s,\.]{3,})", "USD"),
        (r"([\d\s,\.]{3,})\s*(?:usd|доллар|dollars?)", "USD"),
        (r"€\s*([\d\s,\.]{3,})", "EUR"),
        (r"([\d\s,\.]{3,})\s*(?:eur|euro)", "EUR"),
        (r"£\s*([\d\s,\.]{3,})", "GBP"),
        (r"([\d\s,\.]{3,})\s*(?:gbp|pounds?)", "GBP"),
        (r"([\d\s,\.]{4,})\s*(?:원|krw|won)", "KRW"),
        (r"([\d\s,\.]{3,})\s*만원", "KRW_MANWON"),
        (r"([\d\s,\.]{3,})\s*(?:₩)", "KRW"),
        (r"([\d\s,\.]{3,})\s*(?:сум|uzs)", "UZS"),
        (r"가격\s*[:：]?\s*([\d\s,\.]{4,})", "KRW"),
        (r"(?:price|цена|стоимость)\s*[:=]\s*[\"']?([\d\s,\.]{3,})[\"']?", "USD"),
        (r'"price"\s*:\s*"?([\d\s,\.]{3,})"?', "USD"),
    ]
    for pattern, currency in currency_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        raw = _parse_int(match.group(1))
        if not raw:
            continue
        if currency == "KRW_MANWON":
            return raw * 10_000, "KRW"
        return raw, currency

    return None, "KRW"


def _extract_photos(soup: BeautifulSoup, base_url: str) -> list[str]:
    photos: list[str] = []

    og_image = soup.select_one("meta[property='og:image']")
    if og_image and og_image.get("content"):
        photos.append(urljoin(base_url, og_image["content"]))

    for img in soup.select("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-original")
        if not src:
            continue

        full_url = urljoin(base_url, src)
        lower = full_url.lower()
        if not ("http" in lower and any(ext in lower for ext in [".jpg", ".jpeg", ".png", ".webp"])):
            continue

        photos.append(full_url)
        if len(photos) >= 12:
            break

    unique = list(dict.fromkeys(photos))
    return unique[:10]


def parse_car_from_html(html: str, url: str, strict: bool = True) -> CarInfo:
    soup = BeautifulSoup(html, "html.parser")
    full_text = _extract_text(soup)
    script_text = _extract_script_text(soup)
    searchable_text = f"{full_text} {script_text}"

    title = _extract_title(soup)
    brand, model = _extract_brand_model(title)

    year = _extract_year(searchable_text)
    mileage_km = _extract_mileage(searchable_text)
    engine_cc = _extract_engine_cc(searchable_text)
    fuel_type = _extract_fuel_type(searchable_text)
    price_won, price_currency = _extract_price_and_currency(soup, searchable_text)
    photos = _extract_photos(soup, url)

    if engine_cc is None:
        engine_cc = _extract_engine_cc(title)
    if fuel_type is None:
        fuel_type = _extract_fuel_type(title)

    if not strict:
        if year is None:
            year = datetime.now().year
        if mileage_km is None:
            mileage_km = 0

    if engine_cc is None:
        engine_cc = 1600
    if fuel_type is None:
        fuel_type = "Не указано"

    required_fields = {"year": year, "mileage": mileage_km}
    if strict:
        required_fields["price_won"] = price_won
    else:
        # In non-strict mode, allow missing price; default to 0 with RUB currency
        if price_won is None:
            price_won = 0
            price_currency = "RUB"

    missing = [name for name, value in required_fields.items() if value is None]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")

    return CarInfo(
        brand=brand,
        model=model,
        year=year,
        mileage_km=mileage_km,
        engine_cc=engine_cc,
        fuel_type=fuel_type,
        price_won=price_won,
        price_currency=price_currency,
        photos=photos,
        source_url=url,
    )


async def parse_with_fallback(url: str) -> CarInfo:
    html = await fetch_page_html(url, use_playwright=False)
    try:
        return parse_car_from_html(html, url)
    except ValueError:
        rendered_html = await fetch_page_html(url, use_playwright=True)
        return parse_car_from_html(rendered_html, url, strict=False)