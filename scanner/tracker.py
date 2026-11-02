"""Logs every signal and checks the price N candles later (win / loss / tie)."""
import json
import os
import threading
import time

import pandas as pd


def utc_series(df):
    idx = pd.DatetimeIndex(df.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    return pd.Series(df["Close"].values, index=idx)


class Tracker:
    def __init__(self, path="signals_log.json", expiries=(1, 2, 3)):
        self.path, self.expiries = path, tuple(expiries)
        self.lock = threading.RLock()
        self.recs, self.next_id = [], 1
        try:
            with open(path) as f:
                self.recs = json.load(f)
            self.next_id = max([r["id"] for r in self.recs] + [0]) + 1
        except Exception:
            pass

    def _save(self):
        self.recs = self.recs[-5000:]
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.recs, f)
        os.replace(tmp, self.path)

    def add(self, **kw):
        with self.lock:
            rec = {"id": self.next_id, **kw, "res": {str(k): None for k in self.expiries}}
            self.next_id += 1
            self.recs.append(rec)
            self._save()

    def resolve(self, entry_data, minutes):
        """Fill in results for signals whose expiry candle now exists. Returns how many were updated."""
        changed, cache = 0, {}
        with self.lock:
            for r in self.recs:
                pend = [k for k, v in r["res"].items() if v is None]
                df = entry_data.get(r["sym"]) if pend else None
                if df is None:
                    continue
                s = cache.setdefault(r["sym"], utc_series(df))
                entry = pd.Timestamp(r["entry_ts"])
                for k in pend:
                    target = entry + pd.Timedelta(minutes=minutes * int(k))
                    if target in s.index:
                        v = s.loc[target]
                        close = float(v.iloc[-1] if hasattr(v, "iloc") else v)
                        diff = close - r["price"]
                        if abs(diff) < 1e-12:
                            r["res"][k] = "T"
                        else:
                            up = diff > 0
                            r["res"][k] = "W" if (up and r["dir"] == "BUY") or (not up and r["dir"] == "SELL") else "L"
                        changed += 1
                    elif s.index[-1] > target:          # that candle never existed (gap) so skip it
                        r["res"][k] = "NA"
                        changed += 1
            if changed:
                self._save()
        return changed

    def stats(self, k, payout_pct):
        k = str(k)
        with self.lock:
            recs = list(self.recs)

        def agg(keys_of):
            d = {}
            for r in recs:
                v = r["res"].get(k)
                if v in ("W", "L"):
                    for key in keys_of(r):
                        a = d.setdefault(key, [0, 0])
                        a[0 if v == "W" else 1] += 1
            rows = [{"key": key, "wins": a[0], "losses": a[1], "n": a[0] + a[1],
                     "rate": round(100 * a[0] / (a[0] + a[1]), 1)} for key, a in d.items()]
            return sorted(rows, key=lambda x: -x["n"])

        overall = {}
        for kk in self.expiries:
            c = {"W": 0, "L": 0, "T": 0, "NA": 0, None: 0}
            for r in recs:
                c[r["res"].get(str(kk))] = c.get(r["res"].get(str(kk)), 0) + 1
            n = c["W"] + c["L"]
            overall[str(kk)] = {"wins": c["W"], "losses": c["L"], "ties": c["T"], "pending": c[None],
                                "rate": round(100 * c["W"] / n, 1) if n else None}
        recent = [{"id": r["id"], "ts": r["ts"], "name": r["name"], "dir": r["dir"],
                   "strats": r["strats"], "res": r["res"]} for r in recs[-40:][::-1]]
        return {"k": k, "expiries": list(self.expiries), "breakeven": round(100 / (1 + payout_pct / 100), 1),
                "total": len(recs), "overall": overall,
                "by_pair": agg(lambda r: [r["name"]]), "by_strategy": agg(lambda r: r["strats"]),
                "by_hour": agg(lambda r: [r["hour"]]), "by_dir": agg(lambda r: [r["dir"]]),
                "recent": recent}

    def marks(self, sym, since_ts):
        with self.lock:
            return [{"t": pd.Timestamp(r["entry_ts"]).timestamp(), "dir": r["dir"]}
                    for r in self.recs if r["sym"] == sym and r["ts"] >= since_ts]


_inst = None


def get(expiries=(1, 2, 3)):
    global _inst
    if _inst is None:
        _inst = Tracker(expiries=expiries)
    return _inst
