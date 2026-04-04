# 🚀 Деплой в Railway

## Шаги:

### 1. Подготовка GitHub репо
```bash
cd /Users/osmanovalev/telegram-car-bot
git init
git add .
git commit -m "Initial bot commit"
git remote add origin https://github.com/YOUR_USERNAME/telegram-car-bot.git
git push -u origin main
```

### 2. Регистрация в Railway
1. Перейди на https://railway.app
2. Нажми "Start New Project"
3. Выбери "Deploy from GitHub repo"
4. Авторизуйся через GitHub и выбери репо `telegram-car-bot`

### 3. Установи переменные окружения
В Railway Dashboard → Variables добавь:
```
TELEGRAM_BOT_TOKEN=your_token_here
HTTP_TIMEOUT_SECONDS=20
KRW_PER_USD=1440.20
FIXED_USD_UZS=12091.22
ADMIN_PANEL_KEY=spidoznie_kozyavki
MANAGER_CHAT_URL=https://t.me/DO_sales_manager
AUTOPOST_CHANNEL=@your_channel_username
```

Важно:
- Railway не использует локальный `.env` из компьютера при деплое.
- `.env.example` — это только шаблон для тебя, а не реальные переменные Railway.
- После добавления или изменения переменных в Railway нужен `Redeploy` или `Restart` сервиса.

### 4. Деплой
- Railway автоматически обнаружит `Procfile` и `requirements.txt`
- Нажми "Deploy"
- Готово! Бот работает 24/7

## Что происходит:
- Railway читает `Procfile` и запускает `python -m bot.main`
- Твой бот подключится к Telegram API через polling
- Будет работать неограниченно (free tier)

## Остановка/Перезапуск
- Railway → Deployments → Stop/Restart кнопки

## Логи
- Railway → Logs → видишь логи бота в реальном времени

Всё готово! 🎉

---

## Автопоиск и автопост (5–10 раз в день)

Можно запускать отдельный job командой:

`python -m bot.autopost_runner`

Рекомендуемый вариант для Railway:

1. Создай отдельный service/job для этой команды.
2. Передай те же Variables + дополнительные:
	- `AUTO_SCAN_CONFIG_PATH=autopost_filters.json`
	- `AUTO_SCAN_STATE_PATH=data/autopost_seen.json`
	- `AUTO_SCAN_INTERVAL_MINUTES=` (пусто для single-run)
3. Настрой расписание (cron) на 5–10 запусков в день.

Файл фильтров: [autopost_filters.json](autopost_filters.json)
