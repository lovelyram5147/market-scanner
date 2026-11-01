import pandas as pd
import yfinance as yf


def fetch(symbol: str, interval: str, period: str):
    """Download OHLCV and drop the still-forming last candle."""
    try:
        df = yf.download(symbol, interval=interval, period=period,
                         progress=False, auto_adjust=True, threads=False)
    except Exception as e:
        print(f"[data] {symbol}: {e}")
        return None
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(subset=["Close"])
    if len(df) < 60:
        return None
    return df.iloc[:-1]
