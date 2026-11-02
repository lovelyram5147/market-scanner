"""Multi-timeframe matcher: does the trend direction agree across the timeframes you pick?"""
import pandas as pd

from .data import fetch_many
from .indicators import ema
from .regime import classify

ORDER = ["1m", "5m", "10m", "15m", "30m", "1h"]
# timeframe -> (Yahoo interval, period). Yahoo has no 10m candles, so 10m is built from 5m.
YF = {"1m": ("1m", "2d"), "5m": ("5m", "5d"), "10m": ("5m", "5d"), "15m": ("15m", "10d"),
      "30m": ("30m", "20d"), "1h": ("1h", "1mo")}


def norm(interval):
    return {"60m": "1h"}.get(interval, interval)


def resample10(df):
    """Build 10-minute candles from 5-minute ones, dropping a half-finished last bucket."""
    if df is None or len(df) < 20:
        return None
    out = df.resample("10min").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    last = pd.Timestamp(df.index[-1])
    if last.minute % 10 == 0 and len(out):      # last 5m candle starts a new 10m bucket, so it is incomplete
        out = out.iloc[:-1]
    return out if len(out) >= 60 else None


def build(symbols, tfs, cache):
    """Return {tf: {symbol: DataFrame}}, downloading only what is not already in `cache`."""
    out = {}
    for tf in tfs:
        if tf not in YF:
            continue
        yi, period = YF[tf]
        yi = norm(yi)
        if yi not in cache:
            cache[yi] = fetch_many(symbols, yi, period)
        base = cache[yi]
        out[tf] = {s: resample10(d) for s, d in base.items()} if tf == "10m" else base
    return out


def direction(df, mode, regime_cfg):
    """'UP' / 'DOWN' / None for one timeframe."""
    if df is None or len(df) < 60:
        return None
    if mode == "trend":                           # strict: must be a real trend (ADX etc.)
        r = classify(df, regime_cfg)
        return r["direction"] if r["regime"] == "TREND" else None
    c = df["Close"]
    fast, slow, price = ema(c, 20).iloc[-1], ema(c, 50).iloc[-1], c.iloc[-1]
    if fast > slow and price > slow:
        return "UP"
    if fast < slow and price < slow:
        return "DOWN"
    return None


def evaluate(sym, tfmaps, selected, mode, need, regime_cfg):
    """Returns ({tf: direction}, matched_direction_or_None, how_many_agree)."""
    dirs = {}
    for tf in selected:
        try:
            dirs[tf] = direction(tfmaps.get(tf, {}).get(sym), mode, regime_cfg)
        except Exception:
            dirs[tf] = None
    n = len(selected)
    need = n if not need or need <= 0 else min(int(need), n)
    ups = sum(1 for d in dirs.values() if d == "UP")
    downs = sum(1 for d in dirs.values() if d == "DOWN")
    if n and ups >= need and ups > downs:
        return dirs, "UP", ups
    if n and downs >= need and downs > ups:
        return dirs, "DOWN", downs
    return dirs, None, max(ups, downs)
