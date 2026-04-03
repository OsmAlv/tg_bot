from __future__ import annotations

import json
import logging

from parsers.common import normalize_display_text, parse_car_from_html
from utils.helpers import CarInfo, fetch_page_html

logger = logging.getLogger(__name__)


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
    if year is None and form_year.isdigit():
        year = int(form_year)

    mileage = int(spec.get("mileage") or 0)
    engine_cc = int(spec.get("displacement") or 0)
    fuel_type = _fuel_to_ru(spec.get("fuelName"))

    price_manwon = advert.get("price")
    price_won = int(price_manwon) * 10_000 if price_manwon is not None else 0

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
        key=lambda p: (
            0 if p.get("represent") or p.get("isRepresent") or p.get("representYn") == "Y" else 1,
            int(p.get("sequence") or p.get("index") or p.get("seq") or p.get("no") or 0),
        ),
    )
    if photos_data:
        logger.info("Encar photo keys sample: %s", list(photos_data[0].keys()))
    photos: list[str] = []
    for item in photos_sorted:
        path = item.get("path")
        if not path:
            continue
        photos.append(f"https://ci.encar.com{path}")

    if not all([year, mileage, engine_cc, price_won]):
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
    )


async def parse_encar_listing(url: str) -> CarInfo:
    html = await fetch_page_html(url, use_playwright=False)

    data = _parse_from_preloaded_state(html, url)
    if data:
        return data

    try:
        return parse_car_from_html(html, url)
    except ValueError:
        rendered_html = await fetch_page_html(url, use_playwright=True)
        data = _parse_from_preloaded_state(rendered_html, url)
        if data:
            return data
        return parse_car_from_html(rendered_html, url)