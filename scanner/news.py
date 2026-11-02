"""High-impact news filter using the free ForexFactory weekly calendar (unofficial, may be unavailable)."""
import time
from datetime import datetime, timezone

import requests

from .strength import pair_ccys

URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


def symbol_ccys(sym):
    """Which currencies' news can move this symbol."""
    p = pair_ccys(sym)
    if p:
        return set(p)
    if sym.endswith(".NS") or sym.startswith("^NSE"):
        return set()                                    # no Indian events in this calendar
    return {"USD"}                                      # gold, crypto, USDINR, US indices/stocks


def parse(data):
    out = []
    for e in data or []:
        try:
            ts = datetime.fromisoformat(e["date"]).astimezone(timezone.utc).timestamp()
            out.append({"ts": ts, "ccy": str(e["country"]).upper(), "title": e.get("title", ""),
                        "impact": e.get("impact", "")})
        except Exception:
            continue
    return out


def impacts(level):
    return {"High"} if level == "High" else {"High", "Medium"}


class NewsFilter:
    def __init__(self):
        self.events, self.fetched, self.error = [], 0.0, None

    def refresh(self):
        if time.time() - self.fetched < 1800:
            return
        try:
            r = requests.get(URL, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            self.events, self.error, self.fetched = parse(r.json()), None, time.time()
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            self.fetched = time.time() - 1500           # retry in about 5 minutes

    def blocked(self, sym, cfg, now=None):
        """Title of the news event pausing this symbol right now, else None."""
        now = now or time.time()
        ccys, imp = symbol_ccys(sym), impacts(cfg.get("impact", "High"))
        for e in self.events:
            if e["ccy"] in ccys and e["impact"] in imp:
                if e["ts"] - cfg.get("before", 15) * 60 <= now <= e["ts"] + cfg.get("after", 15) * 60:
                    return f"{e['ccy']} {e['title']}"
        return None

    def upcoming(self, cfg, hours=24, now=None):
        now, imp = now or time.time(), impacts(cfg.get("impact", "High"))
        keep = [e for e in self.events if e["impact"] in imp
                and now - cfg.get("after", 15) * 60 <= e["ts"] <= now + hours * 3600]
        return sorted(keep, key=lambda e: e["ts"])


_inst = None


def get():
    global _inst
    if _inst is None:
        _inst = NewsFilter()
    _inst.refresh()
    return _inst
