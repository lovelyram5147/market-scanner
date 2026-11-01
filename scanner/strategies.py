"""Each strategy inspects the last closed candle and returns (name, direction, detail) or None."""
from .indicators import ema, macd, rsi


def rsi_signal(df, cfg):
    r = rsi(df["Close"], cfg["period"])
    prev, cur = r.iloc[-2], r.iloc[-1]
    if prev < cfg["oversold"] <= cur:
        return "RSI", "BUY", f"RSI back above {cfg['oversold']} ({cur:.1f})"
    if prev > cfg["overbought"] >= cur:
        return "RSI", "SELL", f"RSI back below {cfg['overbought']} ({cur:.1f})"


def ema_cross_signal(df, cfg):
    f, s = ema(df["Close"], cfg["fast"]), ema(df["Close"], cfg["slow"])
    if f.iloc[-2] <= s.iloc[-2] and f.iloc[-1] > s.iloc[-1]:
        return "EMA", "BUY", f"EMA{cfg['fast']} crossed above EMA{cfg['slow']}"
    if f.iloc[-2] >= s.iloc[-2] and f.iloc[-1] < s.iloc[-1]:
        return "EMA", "SELL", f"EMA{cfg['fast']} crossed below EMA{cfg['slow']}"


def macd_signal(df, cfg):
    line, sig = macd(df["Close"])
    if line.iloc[-2] <= sig.iloc[-2] and line.iloc[-1] > sig.iloc[-1]:
        return "MACD", "BUY", "MACD crossed above signal"
    if line.iloc[-2] >= sig.iloc[-2] and line.iloc[-1] < sig.iloc[-1]:
        return "MACD", "SELL", "MACD crossed below signal"


def breakout_signal(df, cfg):
    n = cfg["lookback"]
    hi = df["High"].iloc[-n - 1:-1].max()
    lo = df["Low"].iloc[-n - 1:-1].min()
    c = df["Close"].iloc[-1]
    if c > hi:
        return "Breakout", "BUY", f"Closed above {n}-bar high"
    if c < lo:
        return "Breakout", "SELL", f"Closed below {n}-bar low"


def rsi_pullback_signal(df, cfg):
    """With-trend entry: RSI dips to 'dip' (e.g. 40) and turns back up, or mirror for sells."""
    r = rsi(df["Close"], cfg["period"])
    prev, cur = r.iloc[-2], r.iloc[-1]
    if prev < cfg["dip"] <= cur:
        return "RSI pullback", "BUY", f"RSI turned up from dip ({cur:.0f})"
    if prev > cfg["rise"] >= cur:
        return "RSI pullback", "SELL", f"RSI turned down from rise ({cur:.0f})"


def ema_bounce_signal(df, cfg):
    """Price touches the EMA and closes back in trend direction with a confirming candle."""
    e = ema(df["Close"], cfg["period"]).iloc[-1]
    o, h, l, c = (df[k].iloc[-1] for k in ("Open", "High", "Low", "Close"))
    if l <= e < c and c > o:
        return "EMA bounce", "BUY", f"Bounced off EMA{cfg['period']}"
    if h >= e > c and c < o:
        return "EMA bounce", "SELL", f"Rejected at EMA{cfg['period']}"


REGISTRY = {
    "rsi_pullback": rsi_pullback_signal,
    "ema_bounce": ema_bounce_signal,
    "rsi": rsi_signal,
    "ema_cross": ema_cross_signal,
    "macd": macd_signal,
    "breakout": breakout_signal,
}
