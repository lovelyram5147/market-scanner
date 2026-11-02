"""Currency strength meter from the forex pairs we already download."""
CCY = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"]


def pair_ccys(sym):
    """'EURJPY=X' -> ('EUR', 'JPY'); None for anything that is not a pair of the 8 majors."""
    if sym.endswith("=X") and len(sym) == 8 and sym[:3] in CCY and sym[3:6] in CCY:
        return sym[:3], sym[3:6]
    return None


def compute(trend_data, lookback=36):
    """Returns {currency: score from -100 (weakest) to +100 (strongest)}."""
    parts = {c: [] for c in CCY}
    for sym, df in trend_data.items():
        p = pair_ccys(sym)
        if not p or len(df) <= lookback:
            continue
        chg = float(df["Close"].iloc[-1] / df["Close"].iloc[-1 - lookback] - 1)
        parts[p[0]].append(chg)      # base gains when the pair rises
        parts[p[1]].append(-chg)     # quote loses when the pair rises
    raw = {c: sum(v) / len(v) for c, v in parts.items() if v}
    if not raw:
        return {}
    top = max(abs(x) for x in raw.values()) or 1.0
    return {c: round(100 * x / top, 1) for c, x in raw.items()}
