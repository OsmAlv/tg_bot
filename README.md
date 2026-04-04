# Telegram Car Bot

Simple Telegram bot that parses car pages from:

- Encar
- KB ChaChaCha
- KCar

The bot behavior is intentionally minimal:

- no buttons
- no commands
- no menus
- replies only when a message contains a link

## What the bot does

1. User sends a listing URL.
2. Bot detects marketplace by domain.
3. Bot parses:
   - brand
   - model
   - year
   - mileage
   - engine volume (cc)
   - fuel type
   - price in KRW
   - photos
4. Bot converts KRW to USD via exchange-rate API.
5. Bot calculates final price for Tashkent.
6. Bot sends formatted text + photos.

## Project structure

project/
├── bot/
│   └── main.py
├── parsers/
│   ├── __init__.py
│   ├── common.py
│   ├── encar_parser.py
│   ├── kbchachacha_parser.py
│   └── kcar_parser.py
├── services/
│   ├── __init__.py
│   ├── currency_service.py
│   └── price_calculator.py
├── utils/
│   ├── __init__.py
│   └── helpers.py
├── .env.example
└── requirements.txt

## Pricing logic

- BRV = 412000 UZS
- USD/UZS rate from API
- KRW/USD rate from API

Final formula:

- `car_price_usd = converted KRW`
- `delivery = 5000`
- `subtotal = car_price_usd + delivery + customs`
- `final_price = subtotal + (subtotal * 0.05)`

Customs:

- **Age < 1 year**
  - customs duty = `15% * car_price_usd + 1 * engine_cc`
  - VAT = `12% * (car_price_usd + customs_duty)`
  - utilization fee = `180 BRV`
  - customs service fee = `4 BRV`

- **Age >= 1 year**
  - customs duty = `60% * car_price_usd + 6 * engine_cc`
  - VAT = `12% * (car_price_usd + customs_duty)`
  - utilization fee = `300 BRV`
  - customs service fee = `30 BRV`

Message shows only:

1. Price in Korea
2. Final turnkey price in Tashkent

## Run locally in VS Code (macOS)

1. Open workspace in VS Code.
2. Create venv:
   - `python3.11 -m venv .venv`
3. Activate venv:
   - `source .venv/bin/activate`
4. Install dependencies:
   - `pip install -r requirements.txt`
5. Install Playwright browser:
   - `python -m playwright install chromium`
6. Create env file:
   - `cp .env.example .env`
   - set `TELEGRAM_BOT_TOKEN`
7. Run bot:
   - `python -m bot.main`

## Error handling

The bot handles:

- invalid links
- unsupported domains
- missing page fields
- parser/network exceptions

## Auto scanner (5-10 times per day)

You can run an automatic market scan and post matching cars directly to your channel.

### 1) Configure filters

Edit [autopost_filters.json](autopost_filters.json) with your search URLs and constraints:

- `search_urls` — search pages or direct listing URLs
- `filters.year_min`, `filters.mileage_max`, `filters.fuel_types`
- `filters.price_usd_max`, `filters.final_price_usd_max`
- `max_posts_per_run` — cap for each preset per run

### 2) Set env variables

Add to `.env` (or Railway Variables):

- `AUTOPOST_CHANNEL=@your_channel`
- `AUTO_SCAN_CONFIG_PATH=autopost_filters.json`
- `AUTO_SCAN_STATE_PATH=data/autopost_seen.json`
- `AUTO_SCAN_INTERVAL_MINUTES=` (empty = run once)

### 3) Run once (best for cron)

- `python -m bot.autopost_runner`

### 4) Optional loop mode

Set `AUTO_SCAN_INTERVAL_MINUTES=180` and run:

- `python -m bot.autopost_runner`

The scanner:

- checks Encar / KB / KCar links from search pages
- parses each listing with your existing parsers
- applies your filters
- posts only new matches to channel
- stores already posted URLs in [data/autopost_seen.json](data/autopost_seen.json)