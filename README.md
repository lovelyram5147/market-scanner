# Scalp Scanner → Telegram

Two ways to use this repo:

| | **GitHub mode** (cloud, free) | **PC panel** (local dashboard) |
|---|---|---|
| What it does | Every 5 min, messages you **only** pairs where several timeframes point the same way | Live table, entry signals, results tracker, mini charts, alerts |
| Runs | `.github/workflows/scan.yml` → `python -m scanner.align` | `run_panel.bat` → `python -m scanner.panel` |
| Config | `config.github.yaml` | `config.yaml` |

## GitHub mode: timeframe alignment alerts
Every pair is checked on **1m, 5m, 15m, 30m, 1h**. Each timeframe gets a direction (▲ up / ▼ down / · unclear, by EMA20/50). You are messaged only when timeframes agree, ranked in your order of preference:

1. 🥇 **3 timeframes agree**: `1m·5m·15m`, `5m·15m·30m` or `15m·30m·1h` (if more line up, e.g. `1m·5m·15m·30m`, it shows all of them and ranks higher)
2. 🥈 **2 timeframes agree** (neighbouring ones: `1m·5m`, `5m·15m`, `15m·30m`, `30m·1h`), shown only when no 3-timeframe group agrees
3. **ADX**: last preference. It only orders pairs that are otherwise equal (or drops weak ones if you raise `min_adx`)

Example message:
```
🥇 3 or more timeframes agree
🟢 EURUSD CALL ▲  1m·5m·15m·30m  ADX 31 🆕
    1m▲ 5m▲ 15m▲ 30m▲ 1h·
🥈 2 timeframes agree
🔴 USDCAD PUT ▼  30m·1h  ADX 24
```
🆕 = newly aligned or reversed since last run, ⬆ = moved up from 2 to 3 timeframes. Closed markets (weekends, sessions) are skipped.

### Setup (5 minutes)
1. Make the repo **public** (private repos run out of free Actions minutes at a 5-minute schedule). Your token stays hidden in secrets.
2. Upload every file from this zip, keeping folders. Make sure `.github/workflows/scan.yml` is there.
3. Create a bot with @BotFather, add it to your channel/group as admin.
4. Repo → Settings → Secrets and variables → Actions → add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
5. Actions tab → **Timeframe Alignment Scan** → **Run workflow** (leave "force" ticked). You should get a message within a minute or two. After that it runs by itself every 5 minutes.

### Settings (`config.github.yaml`)
- `tiers.best` / `tiers.second`: which timeframe groups count. Add `10m` to `timeframes` and to a group if you want it.
- `mode: trend`: require a strong trend (ADX) on each timeframe instead of just EMA direction.
- `send_when_empty: true`: also send "nothing aligned". `only_changes: true`: message only when something new appears. `notify_lost: true`: list pairs that stopped aligning.
- `min_adx`, `max_per_tier`, `muted`, `labels` (CALL/PUT wording), and the pair lists under `markets`.

### Twelve Data keys (optional, up to 10)
1. Repo → Settings → Secrets and variables → Actions → new secret **`TWELVEDATA_API_KEYS`** = `key1,key2,key3,...` (comma-separated, up to 10).
2. That's it. `twelvedata.enabled: true` is already set in `config.github.yaml`.

How it works: one request per pair returns 1-minute candles, and 5m/10m/15m/30m/1h are built from them, so **a pair costs 1 credit per scan**. Keys are rotated so no key goes over 8 credits a minute. Each free key has 800 credits a day, so the scanner spreads what is left today evenly over the remaining runs (~288 a day). Pairs it cannot cover in a run come from Yahoo in that run, so nothing goes missing. Priority: pairs that are already aligned first, then whichever pair waited longest. Pairs that Yahoo fails on are retried on Twelve Data.

| Free keys | Pairs per run from Twelve Data (weekday) |
|---|---|
| 10 | ~26 of the 33 supported pairs |
| 5 | ~13 |
| 1 | ~2 |

On weekends forex is closed, so only the 3 crypto pairs use credits. A paid plan (no daily cap, e.g. 55 credits/min) covers every pair every run: set `daily_limit_per_key: null` and `per_minute_per_key: 55`. Indices (NIFTY, Bank Nifty, S&P) and US stocks are not on free keys, so they stay on Yahoo. Keys are only sent in a request header and only a short hash is stored in `state.json`. Check Twelve Data's terms before relying on several free accounts. The footer of each message shows how many pairs came from each source and the credits left.

### Good to know
- GitHub's schedule often starts 5–15 minutes late and sometimes skips a run, so expect roughly every 5–10 minutes. Public repos with no activity for 60 days get their schedule paused; re-enable it in the Actions tab.
- Free Yahoo data can lag 1–2 minutes and can occasionally be throttled on cloud servers. If nothing can be downloaded you get one warning per hour.
- This is a trend-alignment watchlist, not an entry timer. Confirm on your live chart.

## PC panel: how it works
1. **Trend timeframe** (default 5m): every market is classified TREND (up/down), RANGE, FLAT or VOLATILE.
2. **Entry timeframe** (default 1m): for trending pairs it looks for with-trend pullback/momentum entries (RSI pullback, EMA21 bounce, MACD cross, 20-bar breakout). By default 2 must agree.
3. Telegram and the panel get CALL/PUT setups, trend start/reversal alerts, and a ranked "trending now" list. Closed markets are skipped automatically.

## Multi-timeframe matcher
Tick any of **1m, 5m, 10m, 15m, 30m, 1h** above the table. Each pair gets one arrow per timeframe (▲ up, ▼ down, · no clear direction) and a green/red **ALL ↑ / ↓** badge when the ticked timeframes agree. Use the **🧭 Matched** filter to see only those pairs. Options: require a strong trend instead of just EMA direction, require only N of the chosen timeframes, block entry signals that don't match, and send a Telegram alert when a pair becomes matched. Yahoo has no 10m candles, so 10m is built from 5m ones.

## Control panel (recommended on PC)
Double-click **`run_panel.bat`** (or `python -m scanner.panel`) → opens `http://127.0.0.1:8765`.

**Scanner tab:** live table (trend, ADX strength, setup score, currency gap, last CALL/PUT), click a row for a mini chart, TradingView link, 🔇 button to ignore a pair (use the **All** filter to find muted pairs and unmute them).
**Results tab:** win rate of every signal after 1/2/3 candles, by pair, strategy, hour and direction, versus the break-even rate for your payout.

### Every feature has a checkbox (Settings → on the right)
| Group | Checkbox |
|---|---|
| Multi-timeframe matcher | ON/OFF and timeframe buttons 1m · 5m · 10m · 15m · 30m · 1h above the table · Match using (direction or strong trend) · Must agree (0 = all) · Entry signals must match · Telegram alert when a pair matches |
| Cleaner signals | Higher timeframe must agree · Cooldown per pair · Daily signal cap · Only trade chosen sessions · Pause pairs around high-impact news |
| Currency strength | Show strength meter · Forex signals must agree with it |
| Tracking & display | Track results · Mini chart on click · Candle countdown on signals |
| Alerts on this PC | Beep (higher = CALL, lower = PUT) · Spoken "Call EUR JPY" · Desktop popup |
| Windows | Start with Windows · System tray icon (needs `pip install pystray pillow`, done by `run_panel.bat`) |
| Telegram | Send to Telegram · Trend alerts · Entry signals · Summary interval |

Settings are saved in `panel_settings.json`. The same options exist under `features:` in `config.yaml` for the non-panel modes.

## Other ways to run
- **PC, no panel:** `python -m scanner.main --loop 60` (or `run_local.bat`). Don't run it together with the panel.
- **GitHub Actions:** see GitHub mode above (it uses `config.github.yaml`, not `config.yaml`).

## Telegram setup
Create a bot with @BotFather, add it to your channel as admin, then either put the token and chat ID in `.env` (copy `.env.example`) or type them into the panel's Telegram box.

## Limits
- Free Yahoo data can lag 1–2 minutes and has no broker/OTC prices. Confirm on your live chart.
- The news filter uses a free unofficial calendar; if it is down the filter does nothing and the panel says so. It covers USD/EUR/GBP etc. events but not Indian events.
- Result stats use Yahoo prices and need a few hundred signals before they mean much.
- Signals are informational, not financial advice.
