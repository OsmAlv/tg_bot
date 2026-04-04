from __future__ import annotations

import asyncio
import logging
import re

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Message

from parsers import detect_marketplace, parse_listing
from services.currency_service import CurrencyService
from services.price_calculator import PriceCalculator
from utils.helpers import extract_urls, format_money_usd, load_settings, set_env_value


logger = logging.getLogger(__name__)


def build_car_message(
    brand: str,
    model: str,
    year: int,
    mileage_km: int,
    engine_cc: int,
    fuel_type: str,
    price_korea_usd: float,
    final_price_usd: float,
    is_approximate: bool = False,
) -> str:
    title = f"🚘 {brand} {model}".strip()
    message = (
        f"{title}\n\n"
        f"📅 Год: {year}\n"
        f"🛣 Пробег: {mileage_km} km\n"
        f"⚙️ Двигатель: {engine_cc} cc\n"
        f"⛽ Топливо: {fuel_type}\n\n"
        f"🚢 Доставка: от 1 месяца\n\n"
        f"💰 Цена в Корее: {format_money_usd(price_korea_usd)} $\n\n"
        f"Ориентировочная цена под ключ в Ташкент:\n"
        f"{format_money_usd(final_price_usd)} $\n\n"
        "🤝 Для персонального и максимально точного расчета обратитесь к менеджеру."
    )

    if is_approximate:
        message += (
            "\n\n"
            "⚠️ Финальная цена может быть не точной. "
            "Для более точной консультации обратитесь по номеру +998971815150"
        )

    return message


async def _api_call_with_retry(coro_factory, max_retries: int = 3):
    """Call coro_factory() and retry on TelegramRetryAfter, waiting the required time."""
    for attempt in range(max_retries + 1):
        try:
            return await coro_factory()
        except TelegramRetryAfter as exc:
            if attempt >= max_retries:
                raise
            wait = exc.retry_after + 2
            logger.warning("Flood control exceeded, waiting %d seconds (attempt %d/%d)...", wait, attempt + 1, max_retries)
            await asyncio.sleep(wait)


async def _send_result(
    bot: Bot,
    chat_id: int | str,
    text: str,
    photos: list[str],
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if not photos:
        await _api_call_with_retry(lambda: bot.send_message(chat_id, text, reply_markup=reply_markup))
        return

    try:
        media = [InputMediaPhoto(media=photo) for photo in photos[:10]]
        await _api_call_with_retry(lambda: bot.send_media_group(chat_id, media=media))
    except TelegramBadRequest:
        logger.warning("Failed to send photo album", extra={"chat_id": chat_id}, exc_info=True)

    await asyncio.sleep(1)  # small gap between album and caption message
    await _api_call_with_retry(lambda: bot.send_message(chat_id, text, reply_markup=reply_markup))


def register_handlers(
    bot: Bot,
    dp: Dispatcher,
    currency_service: CurrencyService,
    price_calculator: PriceCalculator,
    admin_panel_key: str,
    manager_chat_url: str,
    autopost_channel: str | None,
) -> None:
    authorized_admin_chats: set[int] = set()
    pending_admin_action_by_chat: dict[int, str] = {}

    def _manager_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💬 Написать менеджеру", url=manager_chat_url)],
            ]
        )

    def _admin_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📊 Показать курсы", callback_data="admin:rates")],
                [
                    InlineKeyboardButton(text="💵 Изменить USD/UZS", callback_data="admin:set_usd_uzs"),
                    InlineKeyboardButton(text="₩ Изменить KRW/USD", callback_data="admin:set_krw_usd"),
                ],
                [InlineKeyboardButton(text="🔒 Закрыть админку", callback_data="admin:close")],
            ]
        )

    def _rates_text() -> str:
        usd_uzs_value = (
            f"{currency_service.fixed_usd_uzs:.2f}"
            if currency_service.fixed_usd_uzs is not None
            else "не фиксирован (онлайн)"
        )
        krw_usd_value = (
            f"{currency_service.krw_per_usd:.2f}"
            if currency_service.krw_per_usd is not None
            else "не фиксирован (онлайн)"
        )
        return (
            "Текущие курсы:\n"
            f"• FIXED_USD_UZS: {usd_uzs_value}\n"
            f"• KRW_PER_USD: {krw_usd_value}"
        )

    async def _set_usd_uzs_rate(new_rate: float) -> None:
        currency_service.fixed_usd_uzs = new_rate
        await asyncio.to_thread(set_env_value, "FIXED_USD_UZS", f"{new_rate:.2f}")

    async def _set_krw_per_usd_rate(new_rate: float) -> None:
        currency_service.krw_per_usd = new_rate
        await asyncio.to_thread(set_env_value, "KRW_PER_USD", f"{new_rate:.2f}")

    @dp.callback_query(F.data.startswith("admin:"))
    async def handle_admin_callbacks(callback: CallbackQuery) -> None:
        if callback.message is None:
            await callback.answer()
            return

        chat_id = callback.message.chat.id
        if chat_id not in authorized_admin_chats:
            await callback.answer("Нет доступа", show_alert=True)
            return

        action = callback.data.split(":", 1)[1]

        if action == "rates":
            await callback.message.answer(_rates_text(), reply_markup=_admin_keyboard())
            await callback.answer()
            return

        if action == "set_usd_uzs":
            pending_admin_action_by_chat[chat_id] = "set_usd_uzs"
            await callback.message.answer(
                "Введите новый FIXED_USD_UZS, например: 12091.22\n"
                "или отправьте 'отмена'."
            )
            await callback.answer()
            return

        if action == "set_krw_usd":
            pending_admin_action_by_chat[chat_id] = "set_krw_per_usd"
            await callback.message.answer(
                "Введите новый KRW_PER_USD, например: 1440.20\n"
                "или отправьте 'отмена'."
            )
            await callback.answer()
            return

        if action == "close":
            pending_admin_action_by_chat.pop(chat_id, None)
            authorized_admin_chats.discard(chat_id)
            await callback.message.answer("Админка закрыта.")
            await callback.answer()
            return

        await callback.answer()

    @dp.message(F.text)
    async def handle_link(message: Message) -> None:
        text = message.text or ""
        plain_text = text.strip()
        lower_text = plain_text.lower()
        chat_id = message.chat.id

        if plain_text == admin_panel_key:
            authorized_admin_chats.add(chat_id)
            pending_admin_action_by_chat.pop(chat_id, None)
            await message.answer("Админка открыта.", reply_markup=_admin_keyboard())
            await message.answer(_rates_text())
            return

        # Быстрые команды админки текстом:
        # {key} 12091.22
        # {key} usd 12091.22
        # {key} krw 1440.20
        set_usd_match = re.match(
            rf"^{re.escape(admin_panel_key)}\s+(?:usd\s+)?([0-9]+(?:[\.,][0-9]+)?)$",
            plain_text,
            flags=re.IGNORECASE,
        )
        if set_usd_match:
            new_rate = float(set_usd_match.group(1).replace(",", "."))
            await _set_usd_uzs_rate(new_rate)
            authorized_admin_chats.add(chat_id)
            await message.answer(f"FIXED_USD_UZS обновлен: {new_rate:.2f}", reply_markup=_admin_keyboard())
            return

        set_krw_match = re.match(
            rf"^{re.escape(admin_panel_key)}\s+krw\s+([0-9]+(?:[\.,][0-9]+)?)$",
            plain_text,
            flags=re.IGNORECASE,
        )
        if set_krw_match:
            new_rate = float(set_krw_match.group(1).replace(",", "."))
            await _set_krw_per_usd_rate(new_rate)
            authorized_admin_chats.add(chat_id)
            await message.answer(f"KRW_PER_USD обновлен: {new_rate:.2f}", reply_markup=_admin_keyboard())
            return

        if chat_id in authorized_admin_chats and chat_id in pending_admin_action_by_chat:
            if lower_text in {"отмена", "cancel"}:
                pending_admin_action_by_chat.pop(chat_id, None)
                await message.answer("Изменение отменено.", reply_markup=_admin_keyboard())
                return

            value_match = re.match(r"^([0-9]+(?:[\.,][0-9]+)?)$", plain_text)
            if not value_match:
                await message.answer("Введите число, например 12091.22, или 'отмена'.")
                return

            value = float(value_match.group(1).replace(",", "."))
            action = pending_admin_action_by_chat.pop(chat_id)
            if action == "set_usd_uzs":
                await _set_usd_uzs_rate(value)
                await message.answer(f"FIXED_USD_UZS обновлен: {value:.2f}", reply_markup=_admin_keyboard())
            elif action == "set_krw_per_usd":
                await _set_krw_per_usd_rate(value)
                await message.answer(f"KRW_PER_USD обновлен: {value:.2f}", reply_markup=_admin_keyboard())
            return

        urls = extract_urls(text)

        if not urls:
            return

        BATCH_DELAY = 3  # seconds between links to stay within Telegram rate limits
        BATCH_NOTIFY_THRESHOLD = 3  # suppress per-link status when batch is larger than this
        large_batch = len(urls) > BATCH_NOTIFY_THRESHOLD

        if len(urls) == 1:
            await message.answer("Обрабатываю ссылку, подождите...")
        else:
            await message.answer(f"Нашел {len(urls)} ссылок. Обрабатываю по очереди, это займёт некоторое время...")

        posted_to_channel = 0
        failed_indices: list[int] = []

        for index, url in enumerate(urls, start=1):
            if index > 1:
                await asyncio.sleep(BATCH_DELAY)  # pause between links to avoid flood control

            marketplace = detect_marketplace(url)
            if marketplace is None:
                if not large_batch:
                    await message.answer(f"Ссылка {index}: не удалось распознать ссылку.")
                else:
                    failed_indices.append(index)
                continue

            if len(urls) > 1 and not large_batch:
                await message.answer(f"Обрабатываю ссылку {index}/{len(urls)}...")

            try:
                car = await parse_listing(url, marketplace)
                if car.price_won <= 0 or car.mileage_km <= 0:
                    await message.answer(
                        f"Ссылка {index}: не удалось получить корректные цену или пробег. Отправьте ссылку на карточку авто."
                    )
                    continue

                price_korea_usd = await currency_service.source_to_usd(
                    car.price_won,
                    car.price_currency,
                )
                usd_uzs = await currency_service.usd_to_uzs_rate()

                price_result = price_calculator.calculate(
                    car_price_usd=price_korea_usd,
                    car_year=car.year,
                    engine_cc=car.engine_cc,
                    usd_uzs=usd_uzs,
                    fuel_type=car.fuel_type,
                )

                result_text = build_car_message(
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

                await _send_result(bot, message.chat.id, result_text, car.photos, reply_markup=_manager_keyboard())

                if autopost_channel:
                    try:
                        await asyncio.sleep(1)  # extra gap before posting to channel
                        await _send_result(bot, autopost_channel, result_text, car.photos, reply_markup=_manager_keyboard())
                        posted_to_channel += 1
                        if not large_batch:
                            await message.answer(f"Ссылка {index}: также опубликовал в канал: {autopost_channel}")
                    except Exception as exc:
                        logger.exception("Failed to autopost result to channel", exc_info=exc)
                        if not large_batch:
                            await message.answer(f"Ссылка {index}: результат отправлен вам, но не удалось опубликовать его в канал.")
            except ValueError as exc:
                if not large_batch:
                    await message.answer(f"Ссылка {index}: не удалось обработать ссылку: {exc}")
                else:
                    failed_indices.append(index)
            except TelegramRetryAfter:
                raise  # let it propagate — already retried inside _send_result
            except Exception as exc:
                logger.exception("Unexpected error while parsing listing", exc_info=exc)
                if not large_batch:
                    await message.answer(f"Ссылка {index}: ошибка при парсинге. Попробуйте другую ссылку немного позже.")
                else:
                    failed_indices.append(index)

        # Send summary for large batches
        if large_batch:
            ok_count = len(urls) - len(failed_indices)
            summary = f"✅ Обработано {ok_count}/{len(urls)} ссылок."
            if autopost_channel and posted_to_channel:
                summary += f" Опубликовано в {autopost_channel}: {posted_to_channel}."
            if failed_indices:
                summary += f"\n⚠️ Не удалось обработать ссылки: {', '.join(map(str, failed_indices))}."
            await message.answer(summary)


async def main() -> None:
    settings = load_settings()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()

    currency_service = CurrencyService(
        timeout_seconds=settings.http_timeout_seconds,
        krw_per_usd=settings.krw_per_usd,
        fixed_usd_uzs=settings.fixed_usd_uzs,
    )
    price_calculator = PriceCalculator()
    register_handlers(
        bot,
        dp,
        currency_service,
        price_calculator,
        settings.admin_panel_key,
        settings.manager_chat_url,
        settings.autopost_channel,
    )

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())