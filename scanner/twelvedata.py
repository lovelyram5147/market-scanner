"""Twelve Data client: rotates up to 10 API keys and spreads a daily credit budget over the day.

One request per pair returns 1-minute candles; 5m/10m/15m/30m/1h are built from them locally,
so each pair costs exactly 1 credit per scan (credits are charged per symbol, not per row).
"""
import hashlib
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

URL = "https://api.twelvedata.com/time_series"
RULE = {"5m": ("5min", 5), "10m": ("10min", 10), "15m": ("15min", 15), "30m": ("30min", 30), "1h": ("60min", 60)}


def keys_from_env():
    """TWELVEDATA_API_KEYS = key1,key2,...  (comma or newline separated, max 10)."""
    raw = os.getenv("TWELVEDATA_API_KEYS", "") + "," + os.getenv("TWELVEDATA_API_KEY", "")
    keys = []
    for k in raw.replace("\n", ",").replace(";", ",").split(","):
        k = k.strip()
        if k and k not in keys:
            keys.append(k)
    return keys[:10]


def key_id(k):
    return hashlib.sha1(k.encode()).hexdigest()[:8]       # only this hash is ever stored, never the key


def td_symbol(sym, cfg):
    """Yahoo-style symbol -> Twelve Data symbol, or None when it should stay on Yahoo."""
    m = cfg.get("symbol_map") or {}
    if sym in m:
        return m[sym]
    if sym in set(cfg.get("skip") or []):
        return None
    if sym.endswith("=X") and len(sym) == 8:
        return f"{sym[:3]}/{sym[3:6]}"
    if sym.endswith("-USD"):
        return sym[:-4] + "/USD"
    if sym == "GC=F":
        return "XAU/USD"
    return None


def likely_open(sym, now):
    """Cheap schedule check so no credits are spent on closed markets."""
    if sym.endswith("-USD"):
        return True
    wd, h = now.weekday(), now.hour                       # forex/gold: Fri 22:00 UTC to Sun 22:00 UTC closed
    return not (wd == 5 or (wd == 4 and h >= 22) or (wd == 6 and h < 22))


def derive(df1m, tfs, now=None):
    """Closed 1m candles -> {tf: DataFrame}. Drops the forming minute and any half-built higher candle."""
    now = now or pd.Timestamp.now(tz="UTC")
    df = df1m[df1m.index < now.floor("min")]
    out = {}
    for tf in tfs:
        if tf == "1m":
            out[tf] = df
            continue
        rule, mins = RULE[tf]
        agg = df.resample(rule).agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
        if len(agg) and len(df) and agg.index[-1] + pd.Timedelta(minutes=mins) > df.index[-1] + pd.Timedelta(minutes=1):
            agg = agg.iloc[:-1]
        out[tf] = agg
    return out


def to_frame(values):
    df = pd.DataFrame(values)
    df.index = pd.to_datetime(df["datetime"], utc=True)
    df = df[["open", "high", "low", "close"]].astype(float)
    df.columns = ["Open", "High", "Low", "Close"]
    return df[~df.index.duplicated()].sort_index()


class Pool:
    def __init__(self, keys, cfg, state, now=None):
        self.keys, self.cfg = keys, cfg
        self.ids = [key_id(k) for k in keys]
        self.now = now or datetime.now(timezone.utc)
        self.lock = threading.Lock()
        self.dead = set()                                  # keys that failed/limited during this run
        today = self.now.strftime("%Y-%m-%d")
        st = state.setdefault("td", {})
        if st.get("day") != today:                         # new UTC day: credits reset
            st["day"], st["used"] = today, {}
        st.setdefault("used", {})
        st.setdefault("last", {})
        st.setdefault("blocked", {})
        self.st = st
        self.cap = cfg.get("daily_limit_per_key", 800)     # null = no daily cap (paid plans)
        self.per_min = int(cfg.get("per_minute_per_key", 8))
        self.batch = max(1, int(cfg.get("batch_size", 4)))

    # ---- credits
    def remaining(self, i):
        if self.cap is None:
            return 10 ** 6
        return max(0, self.cap - self.st["used"].get(self.ids[i], 0))

    def alive(self):
        return [i for i in range(len(self.keys)) if i not in self.dead and self.remaining(i) > 0]

    def credits_left(self):
        return None if self.cap is None else sum(self.remaining(i) for i in range(len(self.keys)))

    def budget(self):
        """Credits to spend this run: what is left today, spread evenly over the runs still to come."""
        alive = self.alive()
        if not alive:
            return 0
        rem = sum(self.remaining(i) for i in alive) * (1 - self.cfg.get("reserve_pct", 5) / 100)
        tomorrow = (self.now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        runs_left = max(1, math.ceil((tomorrow - self.now).total_seconds() / (self.cfg.get("run_every_minutes", 5) * 60)))
        return max(0, min(int(rem / runs_left), self.per_min * len(alive)))

    # ---- which symbols get Twelve Data this run
    def eligible(self, symbols):
        now_ts = self.now.timestamp()
        return [s for s in symbols if td_symbol(s, self.cfg) and likely_open(s, self.now)
                and self.st["blocked"].get(s, 0) < now_ts]

    def choose(self, symbols, aligned):
        """Aligned pairs first (their freshness matters most), then whoever waited longest."""
        elig = sorted(self.eligible(symbols), key=lambda s: (0 if s in aligned else 1, self.st["last"].get(s, 0)))
        return elig[: self.budget()]

    # ---- fetching
    def _assign(self, syms):
        plan, load = {}, {i: 0 for i in self.alive()}
        for s in syms:
            open_ = [i for i in load if load[i] < self.per_min and load[i] < self.remaining(i)]
            if not open_:
                break
            i = max(open_, key=lambda i: self.remaining(i) - load[i])
            plan.setdefault(i, []).append(s)
            load[i] += 1
        return plan

    def _http(self, i, tds):
        try:
            r = requests.get(URL, params={"symbol": ",".join(tds), "interval": "1min", "outputsize": self.cfg.get("outputsize", 4800),
                                          "order": "asc", "timezone": "UTC"},
                             headers={"Authorization": f"apikey {self.keys[i]}"}, timeout=45)
            return r.json()
        except Exception as e:                             # never print the exception text: it can contain the URL
            print(f"[td] k{i + 1}: request failed ({type(e).__name__})")
            return None

    def _run_key(self, i, syms):
        got, retry = {}, []
        for n in range(0, len(syms), self.batch):
            chunk = syms[n:n + self.batch]
            if i in self.dead:
                retry += chunk
                continue
            tds = [td_symbol(s, self.cfg) for s in chunk]
            data = self._http(i, tds)
            if not isinstance(data, dict):
                continue
            code, msg = data.get("code"), str(data.get("message", "")).lower()
            whole = data.get("status") == "error" and "values" not in data and not any(t in data for t in tds)
            if whole and code == 429:                      # out of credits (this minute or today)
                with self.lock:
                    self.dead.add(i)
                    if "day" in msg:
                        self.st["used"][self.ids[i]] = self.cap or 0
                print(f"[td] k{i + 1}: credit limit reached ({'daily' if 'day' in msg else 'per-minute'}), rerouting")
                retry += chunk
                continue
            if whole and code in (401, 403) and len(tds) > 1:
                with self.lock:
                    self.dead.add(i)
                print(f"[td] k{i + 1}: key rejected (code {code}), skipping it")
                retry += chunk
                continue
            if len(tds) == 1 and "values" in data:
                data = {tds[0]: data}
            elif len(tds) == 1 and whole:
                data = {tds[0]: data}
                if code == 401:
                    with self.lock:
                        self.dead.add(i)
                    print(f"[td] k{i + 1}: key rejected (code 401), skipping it")
                    retry += chunk
                    continue
            with self.lock:
                self.st["used"][self.ids[i]] = self.st["used"].get(self.ids[i], 0) + len(chunk)
            for s, t in zip(chunk, tds):
                d = data.get(t)
                if isinstance(d, dict) and d.get("values"):
                    try:
                        got[s] = to_frame(d["values"])
                        self.st["last"][s] = time.time()
                        continue
                    except Exception:
                        pass
                c = d.get("code") if isinstance(d, dict) else None
                if c in (400, 403, 404):                   # not on this plan / unknown symbol: stop paying for it today
                    self.st["blocked"][s] = time.time() + 86400
                    print(f"[td] {s}: not available on Twelve Data (code {c}), using Yahoo")
        return {"got": got, "retry": retry}

    def fetch(self, syms):
        got, pending = {}, list(syms)
        for _ in range(2):                                 # second round reroutes work from keys that hit a limit
            plan = self._assign(pending) if pending else {}
            if not plan:
                break
            with ThreadPoolExecutor(max_workers=len(plan)) as ex:
                rets = list(ex.map(lambda kv: self._run_key(*kv), plan.items()))
            pending = []
            for r in rets:
                got.update(r["got"])
                pending += r["retry"]
        return got
