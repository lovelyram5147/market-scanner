# Market Scanner → Telegram

Scans forex, crypto, Indian indices/stocks (Nifty, Bank Nifty…) and US stocks on a schedule and sends BUY/SELL signals to Telegram. Free data via Yahoo Finance, free hosting via GitHub Actions.

## Market regime (the core filter)
Every symbol is classified each scan as **TREND** (up/down), **RANGE**, **FLAT** or **VOLATILE** using ADX/DI, a price-efficiency ratio, EMA20/50 alignment and ATR vs its own history. **You only get Telegram messages for TREND markets:**
- 📈/📉 a market starts trending or reverses direction
- 🟢/🔴 a strategy signal that agrees with the trend (BUY in uptrend, SELL in downtrend)
- optional: trend-faded notices and a list of all trending markets (see `alerts:` in `config.yaml`)

Tune thresholds under `regime:` in `config.yaml`.

## Strategies (on closed candles)
RSI re-entry · EMA 9/21 cross · MACD cross · 20-bar breakout. Set `min_confluence: 2` in `config.yaml` to alert only when strategies agree.

## Setup
1. Telegram: message **@BotFather** → `/newbot` → copy the token. Send any message to your bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your `chat.id`.
2. GitHub repo → Settings → Secrets and variables → Actions → add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
3. Edit `config.yaml` (symbols, timeframes, strategies).
4. Actions tab → **Market Scan** → Run workflow. It then runs every 15 min.

## Run locally
```bash
pip install -r requirements.txt
cp .env.example .env && export $(cat .env | xargs)
python -m scanner.main --test   # test Telegram
python -m scanner.main          # run a scan
```

## Symbols
Yahoo tickers: forex `EURUSD=X`, crypto `BTC-USD`, Nifty `^NSEI`, Bank Nifty `^NSEBANK`, NSE stocks `RELIANCE.NS`.

## Notes
- Signals are informational, not financial advice.
- Yahoo intraday data can be delayed ~15 min for some exchanges.
- Add a new strategy: write a function in `scanner/strategies.py` and register it in `REGISTRY` + `config.yaml`.
