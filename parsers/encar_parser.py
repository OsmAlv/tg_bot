from __future__ import annotations

import json
import logging
import re
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from parsers.common import normalize_display_text, parse_car_from_html
from utils.helpers import CarInfo, fetch_page_html

logger = logging.getLogger(__name__)


def _extract_carid(url: str) -> str | None:
    parsed = urlparse(url)
    query_carid = parse_qs(parsed.query).get("carid", [None])[0]
    if query_carid and str(query_carid).isdigit():
        return str(query_carid)

    path_match = re.search(r"/cars/detail/(\d+)", parsed.path)
    if path_match:
        return path_match.group(1)

    raw_match = re.search(r"carid=(\d+)", url)
    if raw_match:
        return raw_match.group(1)

    return None


def _build_fem_detail_url(carid: str) -> str:
    return f"https://fem.encar.com/cars/detail/{carid}"


def _to_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value)
    digits = re.sub(r"[^0-9]", "", text)
    return int(digits) if digits else None


def _extract_json_object(text: str, start_index: int) -> str | None:
    brace_start = text.find("{", start_index)
    if brace_start == -1:
        return None

    level = 0
    in_string = False
    escape = False

    for idx in range(brace_start, len(text)):
        char = text[idx]

        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue

        if char == "{":
            level += 1
        elif char == "}":
            level -= 1
            if level == 0:
                return text[brace_start : idx + 1]

    return None


def _fuel_to_ru(fuel_name: str | None) -> str:
    if not fuel_name:
        return "Не указано"

    key = fuel_name.strip().lower()
    mapping = {
        "가솔린": "Бензин",
        "휘발유": "Бензин",
        "diesel": "Дизель",
        "디젤": "Дизель",
        "경유": "Дизель",
        "lpg": "Газ",
        "엘피지": "Газ",
        "전기": "Электро",
        "electric": "Электро",
        "hybrid": "Гибрид",
        "하이브리드": "Гибрид",
    }
    return mapping.get(key, fuel_name)


def _merge_model_parts(model_base: str, grade: str) -> str:
    base = normalize_display_text(model_base)
    extra = normalize_display_text(grade)

    if not base:
        return extra
    if not extra:
        return base

    base_lower = base.lower()
    extra_lower = extra.lower()
    if extra_lower in base_lower:
        return base
    if base_lower in extra_lower:
        return extra

    # Убираем дублирующиеся слова, сохраняя порядок
    seen: set[str] = set()
    merged: list[str] = []
    for token in f"{base} {extra}".split():
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(token)
    return " ".join(merged)


def _extract_fallback_fields(html: str) -> tuple[int | None, int | None, int | None, int | None]:
    soup = BeautifulSoup(html, "html.parser")

    text_parts: list[str] = []
    for meta_name in ["description", "og:description", "og:title"]:
        node = soup.select_one(f"meta[name='{meta_name}'], meta[property='{meta_name}']")
        if node and node.get("content"):
            text_parts.append(str(node.get("content")))
    text_parts.append(" ".join(soup.stripped_strings))
    text = " ".join(text_parts)

    year: int | None = None
    production_year_month: int | None = None

    ym_match = re.search(r"\b(19\d{2}|20\d{2})\s*[년/.-]\s*(0?[1-9]|1[0-2])(?:\s*월|\s*식)?", text)
    if ym_match:
        year = int(ym_match.group(1))
        month = int(ym_match.group(2))
        production_year_month = year * 100 + month
    else:
        short_match = re.search(r"\b(\d{2})\s*/\s*(0?[1-9]|1[0-2])\s*식", text)
        if short_match:
            year = 2000 + int(short_match.group(1))
            month = int(short_match.group(2))
            production_year_month = year * 100 + month

    mileage_km: int | None = None
    mileage_match = re.search(r"([\d,\.]+)\s*km", text, flags=re.IGNORECASE)
    if mileage_match:
        mileage_km = _to_int(mileage_match.group(1))

    price_won: int | None = None
    manwon_match = re.search(r"([\d,\.]+)\s*만원", text)
    if manwon_match:
        manwon = _to_int(manwon_match.group(1))
        if manwon is not None:
            price_won = manwon * 10_000

    return year, production_year_month, mileage_km, price_won


def _parse_from_preloaded_state(html: str, url: str) -> CarInfo | None:
    marker = "__PRELOADED_STATE__"
    marker_index = html.find(marker)
    if marker_index == -1:
        return None

    json_text = _extract_json_object(html, marker_index)
    if not json_text:
        return None

    payload = json.loads(json_text)
    cars = payload.get("cars", {})
    base = cars.get("base", {})
    category = base.get("category", {})
    spec = base.get("spec", {})
    advert = base.get("advertisement", {})
    photos_data = base.get("photos", [])

    year_month = str(category.get("yearMonth") or "")
    form_year = str(category.get("formYear") or "")
    year = int(year_month[:4]) if len(year_month) >= 4 and year_month[:4].isdigit() else None
    production_year_month = int(year_month[:6]) if len(year_month) >= 6 and year_month[:6].isdigit() else None
    if year is None and form_year.isdigit():
        year = int(form_year)

    mileage = _to_int(spec.get("mileage"))
    engine_cc = _to_int(spec.get("displacement"))
    fuel_type = _fuel_to_ru(spec.get("fuelName"))

    price_manwon = _to_int(advert.get("price"))
    price_won = price_manwon * 10_000 if price_manwon is not None else None

    fb_year, fb_year_month, fb_mileage, fb_price_won = _extract_fallback_fields(html)
    if year is None:
        year = fb_year
    if production_year_month is None:
        production_year_month = fb_year_month
    if mileage is None:
        mileage = fb_mileage
    if price_won is None:
        price_won = fb_price_won
    if engine_cc is None:
        engine_cc = 1600

    brand = (
        category.get("manufacturerEnglishName")
        or category.get("manufacturerName")
        or "Unknown"
    )
    model_base = (
        category.get("modelEnglishName")
        or category.get("modelGroupEnglishName")
        or category.get("modelName")
        or category.get("modelGroupName")
        or ""
    )
    grade = category.get("gradeEnglishName") or category.get("gradeName") or ""
    model = _merge_model_parts(str(model_base), str(grade))

    brand = normalize_display_text(str(brand)) or "Unknown"
    model = normalize_display_text(str(model))

    photos_sorted = sorted(
        photos_data,
        key=lambda p: str(p.get("code") or ""),
    )
    photos: list[str] = []
    for item in photos_sorted:
        path = item.get("path")
        if not path:
            continue
        photos.append(f"https://ci.encar.com{path}")

    if year is None or mileage is None or price_won is None:
        return None

    return CarInfo(
        brand=brand,
        model=model,
        year=int(year),
        mileage_km=int(mileage),
        engine_cc=int(engine_cc),
        fuel_type=str(fuel_type),
        price_won=int(price_won),
        photos=list(dict.fromkeys(photos))[:10],
        source_url=url,
        production_year_month=production_year_month,
    )


async def parse_encar_listing(url: str) -> CarInfo:
    carid = _extract_carid(url)
    candidate_urls: list[str] = [url]
    if carid:
        fem_url = _build_fem_detail_url(carid)
        if fem_url not in candidate_urls:
            candidate_urls.append(fem_url)

    last_error: Exception | None = None
    for candidate in candidate_urls:
        try:
            html = await fetch_page_html(candidate, use_playwright=False)
            data = _parse_from_preloaded_state(html, candidate)
            if data:
                data.source_url = url
                return data
            parsed = parse_car_from_html(html, candidate)
            parsed.source_url = url
            return parsed
        except Exception as exc:
            last_error = exc

    for candidate in candidate_urls:
        try:
            html = await fetch_page_html(candidate, use_playwright=True)
            data = _parse_from_preloaded_state(html, candidate)
            if data:
                data.source_url = url
                return data
            parsed = parse_car_from_html(html, candidate)
            parsed.source_url = url
            return parsed
        except Exception as exc:
            last_error = exc

    if isinstance(last_error, Exception):
        raise last_error
    raise ValueError("Unable to parse Encar listing")