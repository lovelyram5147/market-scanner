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


REGISTRY = {
    "rsi": rsi_signal,
    "ema_cross": ema_cross_signal,
    "macd": macd_signal,
    "breakout": breakout_signal,
}
