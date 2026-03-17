from __future__ import annotations

from enum import Enum
from parsers.common import parse_with_fallback

from parsers.encar_parser import parse_encar_listing
from parsers.kbchachacha_parser import parse_kbchachacha_listing
from parsers.kcar_parser import parse_kcar_listing
from utils.helpers import CarInfo


class Marketplace(str, Enum):
    ENCAR = "encar"
    KB = "kbchachacha"
    KCAR = "kcar"
    GENERIC = "generic"


def detect_marketplace(url: str) -> Marketplace | None:
    lower_url = url.lower()
    if "encar.com" in lower_url:
        return Marketplace.ENCAR
    if "kbchachacha.com" in lower_url:
        return Marketplace.KB
    if "kcar.com" in lower_url:
        return Marketplace.KCAR
    if lower_url.startswith("http://") or lower_url.startswith("https://"):
        return Marketplace.GENERIC
    return None


async def parse_listing(url: str, marketplace: Marketplace) -> CarInfo:
    if marketplace == Marketplace.ENCAR:
        return await parse_encar_listing(url)
    if marketplace == Marketplace.KB:
        return await parse_kbchachacha_listing(url)
    if marketplace == Marketplace.KCAR:
        return await parse_kcar_listing(url)
    if marketplace == Marketplace.GENERIC:
        return await parse_with_fallback(url)
    raise ValueError("Unsupported marketplace")