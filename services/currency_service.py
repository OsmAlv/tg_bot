from __future__ import annotations

import asyncio
import time

import requests


class CurrencyService:
    def __init__(
        self,
        timeout_seconds: int = 20,
        krw_per_usd: float | None = None,
        fixed_usd_uzs: float | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.krw_per_usd = krw_per_usd
        self.fixed_usd_uzs = fixed_usd_uzs
        self._cache: dict[str, tuple[float, float]] = {}
        self._cache_ttl_seconds = 900

    def _get_cached(self, key: str) -> float | None:
        value = self._cache.get(key)
        if not value:
            return None
        cached_at, rate = value
        if time.time() - cached_at > self._cache_ttl_seconds:
            return None
        return rate

    def _set_cached(self, key: str, rate: float) -> None:
        self._cache[key] = (time.time(), rate)

    def _fetch_rate_sync(self, base_currency: str, target_currency: str) -> float:
        url = f"https://open.er-api.com/v6/latest/{base_currency}"
        response = requests.get(url, timeout=self.timeout_seconds)
        response.raise_for_status()
        payload = response.json()

        rates = payload.get("rates", {})
        rate = rates.get(target_currency)
        if not rate:
            raise ValueError(f"Failed to get exchange rate: {base_currency}->{target_currency}")

        return float(rate)

    async def _get_rate(self, base_currency: str, target_currency: str) -> float:
        cache_key = f"{base_currency}_{target_currency}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        rate = await asyncio.to_thread(self._fetch_rate_sync, base_currency, target_currency)
        self._set_cached(cache_key, rate)
        return rate

    async def krw_to_usd_rate(self) -> float:
        if self.krw_per_usd is not None:
            return 1.0 / self.krw_per_usd
        return await self._get_rate("KRW", "USD")

    async def usd_to_uzs_rate(self) -> float:
        if self.fixed_usd_uzs is not None:
            return self.fixed_usd_uzs
        return await self._get_rate("USD", "UZS")

    async def krw_to_usd(self, amount_krw: int | float) -> float:
        value = float(amount_krw)
        if self.krw_per_usd is not None:
            return value / self.krw_per_usd
        rate = await self._get_rate("KRW", "USD")
        return value * rate

    async def source_to_usd(self, amount: int | float, currency: str = "KRW") -> float:
        normalized = (currency or "KRW").upper()
        value = float(amount)

        if normalized == "USD":
            return value
        if normalized == "KRW":
            return await self.krw_to_usd(value)
        if normalized == "RUB":
            # If we have a fixed USD/UZS rate, use a typical RUB/USD rate
            # Otherwise fetch it dynamically
            rate = await self._get_rate("RUB", "USD")
            return value * rate

        rate = await self._get_rate(normalized, "USD")
        return value * rate