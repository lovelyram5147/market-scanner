"""Classify a market as TREND (up/down), RANGE, FLAT or VOLATILE."""
from .indicators import adx, efficiency_ratio, ema


def classify(df, cfg):
    c = df["Close"]
    adx_s, pdi, mdi, atr = adx(df, cfg["adx_period"])
    atr_pct = atr / c
    base = atr_pct.iloc[-100:]
    vol_ratio = float(atr_pct.iloc[-1] / base.median())     # current vs normal volatility
    vol_rank = float((base < atr_pct.iloc[-1]).mean())      # 0 = quietest, 1 = wildest
    er = float(efficiency_ratio(c, 20).iloc[-1])
    er_long = float(efficiency_ratio(c, 50).iloc[-1])   # rules out swings inside a range
    e_fast, e_slow = ema(c, 20).iloc[-1], ema(c, 50).iloc[-1]
    a = float(adx_s.iloc[-1])
    price = float(c.iloc[-1])

    up = pdi.iloc[-1] > mdi.iloc[-1] and e_fast > e_slow and price > e_slow
    down = pdi.iloc[-1] < mdi.iloc[-1] and e_fast < e_slow and price < e_slow

    if (a >= cfg["trend_adx"] and er >= cfg["min_efficiency"]
            and er_long >= cfg["min_efficiency_long"] and (up or down)):
        regime, direction = "TREND", ("UP" if up else "DOWN")
    elif vol_ratio >= cfg["volatile_ratio"]:
        regime, direction = "VOLATILE", None     # big, directionless swings
    elif vol_rank <= cfg["flat_percentile"] and a < cfg["range_adx"]:
        regime, direction = "FLAT", None         # dead, tiny candles
    else:
        regime, direction = "RANGE", None        # normal movement, no direction

    return {"regime": regime, "direction": direction, "adx": a, "er": er,
            "vol_ratio": vol_ratio}
