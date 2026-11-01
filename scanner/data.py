import pandas as pd
import yfinance as yf

MINUTES = {"1m": 1, "2m": 2, "5m": 5, "15m": 15, "30m": 30, "60m": 60, "1h": 60}


def fetch_many(symbols, interval, period):
    """One batched download for all symbols. Returns {symbol: DataFrame of CLOSED candles}."""
    out = {}
    if not symbols:
        return out
    try:
        raw = yf.download(tickers=" ".join(symbols), interval=interval, period=period,
                          group_by="ticker", progress=False, auto_adjust=True, threads=True)
    except Exception as e:
        print(f"[data] download failed ({interval}): {e}")
        return out
    if raw is None or raw.empty:
        return out
    for s in symbols:
        cols = raw.columns
        if isinstance(cols, pd.MultiIndex):
            if s in cols.get_level_values(0):
                df = raw[s].copy()
            elif s in cols.get_level_values(1):
                df = raw.xs(s, axis=1, level=1).copy()
            else:
                continue
        else:
            df = raw.copy()
        df = df.dropna(subset=["Close"])
        if len(df) < 60:
            continue
        out[s] = df.iloc[:-1]          # drop the still-forming candle
    return out


def is_fresh(df, interval, max_bars=5):
    """False when the market is closed / data is stale (weekends, holidays, session end)."""
    ts = pd.Timestamp(df.index[-1])
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    age_min = (pd.Timestamp.now(tz="UTC") - ts).total_seconds() / 60
    return age_min <= MINUTES.get(interval, 5) * max_bars
