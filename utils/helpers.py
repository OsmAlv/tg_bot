from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass

import requests
from dotenv import load_dotenv


logger = logging.getLogger(__name__)


@dataclass
class Settings:
    telegram_bot_token: str
    http_timeout_seconds: int = 20
    krw_per_usd: float | None = None
    fixed_usd_uzs: float | None = None
    admin_panel_key: str = "spidoznie_kozyavki"
    manager_chat_url: str = "https://t.me/DO_sales_manager"
    autopost_channel: str | None = None


@dataclass
class CarInfo:
    brand: str
    model: str
    year: int
    mileage_km: int
    engine_cc: int
    fuel_type: str
    price_won: int
    photos: list[str]
    source_url: str
    price_currency: str = "KRW"
    production_year_month: int | None = None


def load_settings() -> Settings:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set in .env")

    timeout = int(os.getenv("HTTP_TIMEOUT_SECONDS", "20"))

    krw_per_usd_raw = os.getenv("KRW_PER_USD", "").strip()
    try:
        krw_per_usd = float(krw_per_usd_raw) if krw_per_usd_raw else None
    except ValueError:
        krw_per_usd = None

    fixed_usd_uzs_raw = os.getenv("FIXED_USD_UZS", "").strip()
    try:
        fixed_usd_uzs = float(fixed_usd_uzs_raw) if fixed_usd_uzs_raw else None
    except ValueError:
        fixed_usd_uzs = None
    admin_panel_key = os.getenv("ADMIN_PANEL_KEY", "spidoznie_kozyavki").strip() or "spidoznie_kozyavki"
    manager_chat_url = os.getenv("MANAGER_CHAT_URL", "https://t.me/DO_sales_manager").strip() or "https://t.me/DO_sales_manager"
    autopost_channel_raw = os.getenv("AUTOPOST_CHANNEL", "").strip()
    autopost_channel = autopost_channel_raw or None
    return Settings(
        telegram_bot_token=token,
        http_timeout_seconds=timeout,
        krw_per_usd=krw_per_usd,
        fixed_usd_uzs=fixed_usd_uzs,
        admin_panel_key=admin_panel_key,
        manager_chat_url=manager_chat_url,
        autopost_channel=autopost_channel,
    )


def set_env_value(key: str, value: str, env_path: str = ".env") -> None:
    lines: list[str] = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()

    updated = False
    new_lines: list[str] = []
    prefix = f"{key}="
    for line in lines:
        if line.startswith(prefix):
            new_lines.append(f"{key}={value}")
            updated = True
        else:
            new_lines.append(line)

    if not updated:
        new_lines.append(f"{key}={value}")

    with open(env_path, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines) + "\n")


def extract_urls(text: str) -> list[str]:
    url_regex = r"https?://[^\s]+"
    return [match.strip() for match in re.findall(url_regex, text)]


def extract_first_url(text: str) -> str | None:
    urls = extract_urls(text)
    return urls[0] if urls else None


def format_money_usd(value: float) -> str:
    return f"{value:,.0f}".replace(",", "")


def _fetch_page_html_sync(url: str, timeout_seconds: int = 20) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        )
    }
    response = requests.get(url, headers=headers, timeout=timeout_seconds)
    response.raise_for_status()
    return response.text


async def _fetch_with_playwright(url: str, timeout_seconds: int = 30) -> str:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        raise ValueError("Playwright is not available for dynamic page rendering") from exc

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(url, wait_until="networkidle", timeout=timeout_seconds * 1000)
            # Extra wait for heavy JS-rendered sites like auto.ru
            await asyncio.sleep(2)
            html = await page.content()
            await context.close()
            await browser.close()
            return html
    except Exception as exc:
        error_text = str(exc)
        if "Executable doesn't exist" in error_text:
            await _install_playwright_browser_runtime()
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context()
                page = await context.new_page()
                await page.goto(url, wait_until="networkidle", timeout=timeout_seconds * 1000)
                await asyncio.sleep(2)
                html = await page.content()
                await context.close()
                await browser.close()
                return html
        raise ValueError("Playwright rendering failed") from exc


async def _install_playwright_browser_runtime() -> None:
    browser_path = os.getenv("PLAYWRIGHT_BROWSERS_PATH", "/app/.playwright")
    logger.warning("Playwright browser binary missing, installing Chromium to %s", browser_path)

    def _install() -> None:
        env = os.environ.copy()
        env["PLAYWRIGHT_BROWSERS_PATH"] = browser_path
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
            env=env,
        )

    await asyncio.to_thread(_install)


async def fetch_page_html(url: str, use_playwright: bool = False, timeout_seconds: int = 20) -> str:
    if use_playwright:
        return await _fetch_with_playwright(url, timeout_seconds=max(timeout_seconds, 30))
    return await asyncio.to_thread(_fetch_page_html_sync, url, timeout_seconds)