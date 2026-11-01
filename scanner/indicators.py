def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def macd(s, fast=12, slow=26, signal=9):
    line = ema(s, fast) - ema(s, slow)
    return line, ema(line, signal)


def adx(df, n=14):
    """Returns (adx, +DI, -DI, atr)."""
    import pandas as pd
    h, l, c = df["High"], df["Low"], df["Close"]
    up, dn = h.diff(), -l.diff()
    plus = ((up > dn) & (up > 0)).astype(float) * up
    minus = ((dn > up) & (dn > 0)).astype(float) * dn
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    a = 1 / n
    atr = tr.ewm(alpha=a, adjust=False).mean()
    pdi = 100 * plus.ewm(alpha=a, adjust=False).mean() / atr
    mdi = 100 * minus.ewm(alpha=a, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    return dx.ewm(alpha=a, adjust=False).mean(), pdi, mdi, atr


def efficiency_ratio(s, n=20):
    """1 = straight line move, 0 = pure noise."""
    return (s - s.shift(n)).abs() / s.diff().abs().rolling(n).sum()
