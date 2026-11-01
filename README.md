# Scalp Scanner → Telegram

Built for **binary / scalping / short intraday** trading with live charts. It scans ~38 markets (all 28 major/minor/cross forex pairs, USDINR, gold, 3 crypto, a few indices/stocks) and tells you **which pair to put on your chart and which way**, so the chart stays your decision tool.

## How it works
1. **Trend timeframe** (default 5m): every market is classified as TREND (up/down), RANGE, FLAT or VOLATILE (ADX, DI, EMA20/50, efficiency ratio, ATR).
2. **Entry timeframe** (default 1m): only for trending pairs, it looks for pullback/momentum entries *with* the trend: RSI pullback, EMA21 bounce, MACD cross, 20-bar breakout. By default 2 must agree (`min_confluence`).
3. Telegram gets: `CALL`/`PUT` setups, trend start/reversal alerts, and a ranked "trending now" list every 15 min. Range/flat/volatile pairs stay silent. Closed markets (weekends, sessions) are skipped automatically.

## Two ways to run
**A. On your PC (recommended for 1m scalping)**: checks every 60 seconds.
```bash
pip install -r requirements.txt
copy .env.example .env      # then put your token and chat id inside
python -m scanner.main --test      # Telegram test
python -m scanner.main --loop 60   # run continuously (or double-click run_local.bat)
```
**B. GitHub Actions (free cloud, slower)**: uses `config.cloud.yaml` (15m trend / 5m entry) every ~5 min. Add repo secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`, then Actions → Market Scan → Run workflow. Use a public repo to avoid the free-minute limit.

GitHub can't run every minute and often starts late, so it is too slow for 1m / binary entries.

## Tuning (`config.yaml`)
- `min_confluence: 3` → fewer, cleaner alerts. `max_alerts_per_scan` caps message size.
- `trend_adx` higher → only stronger trends.
- `alerts.summary_every_minutes: 0` → turn off the ranked list.
- `alerts.expiry_hint` is just a reminder line; edit or delete it.
- Edit the pair lists under `markets:` freely (Yahoo tickers: `EURUSD=X`, `BTC-USD`, `^NSEI`, `RELIANCE.NS`).

## Limits
- Free Yahoo data can lag 1–2 minutes and has no broker/OTC prices (e.g. Olymp Trade OTC pairs). Always confirm on your live chart before trading.
- Signals are informational, not financial advice. Test before risking money.
