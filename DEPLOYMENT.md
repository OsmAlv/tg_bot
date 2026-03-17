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
```

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
