from parsers.common import parse_with_fallback
from utils.helpers import CarInfo


async def parse_kcar_listing(url: str) -> CarInfo:
    return await parse_with_fallback(url)