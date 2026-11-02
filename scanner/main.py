import argparse
import html
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone

import yaml

from . import mtf
from . import news as newsmod
from . import tracker as trk
from .data import MINUTES, fetch_many, is_fresh
from .notifier import send
from .regime import classify
from .strategies import REGISTRY
from .strength import compute as strength_compute
from .strength import pair_ccys

STATE_FILE = "state.json"
NAMES = {"^NSEI": "NIFTY 50", "^NSEBANK": "BANK NIFTY", "^GSPC": "S&P 500", "GC=F": "GOLD"}
SESSIONS = {"asia": (22, 8), "london": (7, 16), "newyork": (12, 22)}   # UTC hours


def display_name(sym):
    return NAMES.get(sym, sym.replace("=X", ""))


def pretty(sym):
    return html.escape(display_name(sym))


def load_env(path=".env"):
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_state():
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
        if "seen" in s and "regime" in s:
            s.setdefault("last_sig", {})
            s.setdefault("daily", {})
            s.setdefault("mtf", {})
            return s
    except Exception:
        pass
    return {"seen": {}, "regime": {}, "last_sig": {}, "daily": {}, "mtf": {}}


def save_state(state):
    state["seen"] = dict(list(state["seen"].items())[-3000:])
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def session_open(ft, now=None):
    sc = ft.get("sessions") or {}
    active = sc.get("active") or []
    if not sc.get("enabled") or not active:
        return True
    h = (now or datetime.now(timezone.utc)).hour
    for name in active:
        a, b = SESSIONS.get(name, (0, 0))
        if (a <= h < b) if a < b else (h >= a or h < b):
            return True
    return False


def scan(cfg, sink=None):
    state = load_state()
    al, ft = cfg["alerts"], cfg.get("features") or {}
    tfs = cfg["timeframes"]
    t_cfg, e_cfg, h_cfg = tfs["trend"], tfs["entry"], tfs.get("htf")
    muted = set(cfg.get("muted") or [])
    all_pairs = [(g, s) for g, lst in cfg["markets"].items() for s in lst]
    pairs = [(g, s) for g, s in all_pairs if s not in muted]
    symbols = [s for _, s in pairs]

    trend_data = fetch_many(symbols, t_cfg["interval"], t_cfg["period"])
    entry_data = fetch_many(symbols, e_cfg["interval"], e_cfg["period"])
    htf_on = bool(ft.get("htf_confirm")) and bool(h_cfg)
    htf_data = fetch_many(symbols, h_cfg["interval"], h_cfg["period"]) if htf_on else {}

    strength = {}
    if ft.get("strength_meter") or ft.get("strength_filter"):
        strength = strength_compute(trend_data, ft.get("strength_lookback", 36))

    mt = ft.get("mtf") or {}
    mtf_tfs = [t for t in mtf.ORDER if t in (mt.get("timeframes") or [])]
    mtf_on = bool(mt.get("enabled")) and bool(mtf_tfs)
    tfmaps = {}
    if mtf_on:
        cache = {mtf.norm(t_cfg["interval"]): trend_data, mtf.norm(e_cfg["interval"]): entry_data}
        if htf_on:
            cache[mtf.norm(h_cfg["interval"])] = htf_data
        tfmaps = mtf.build(symbols, mtf_tfs, cache)

    nf = ft.get("news") or {}
    nw = newsmod.get() if nf.get("enabled") else None
    session_ok = session_open(ft)
    tracker = trk.get(tuple(ft.get("tracker_expiries", (1, 2, 3)))) if ft.get("tracker") else None
    resolved = tracker.resolve(entry_data, MINUTES[e_cfg["interval"]]) if tracker else 0

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily, lastsig = state["daily"], state["last_sig"]
    if daily.get("date") != today:
        daily.clear()
        daily.update(date=today, count=0)

    events, ranked, counts = [], [], defaultdict(int)
    rows, row_by = [], {}
    for market, sym in all_pairs:
        row = {"sym": sym, "name": display_name(sym), "market": market,
               "status": "muted" if sym in muted else "nodata"}
        rows.append(row)
        row_by[sym] = row

    for market, sym in pairs:
        row = row_by[sym]
        dt, de = trend_data.get(sym), entry_data.get(sym)
        if dt is None or de is None:
            print(f"[scan] no data: {sym}")
            continue
        if not is_fresh(de, e_cfg["interval"], cfg.get("max_age_bars", 5)):
            row["status"] = "closed"
            continue
        try:
            rt, re_ = classify(dt, cfg["regime"]), classify(de, cfg["regime"])
        except Exception as e:
            print(f"[scan] {sym} regime error: {e}")
            continue

        counts[rt["regime"]] += 1
        label = f"TREND_{rt['direction']}" if rt["regime"] == "TREND" else rt["regime"]
        rkey = f"{sym}|{t_cfg['interval']}"
        prev = state["regime"].get(rkey)
        state["regime"][rkey] = label
        print(f"[regime] {sym:10} {t_cfg['interval']:3} {label:11} ADX {rt['adx']:.0f}  ER {rt['er']:.2f}")

        base = dict(market=market, sym=sym, price=float(de["Close"].iloc[-1]), r=rt)
        row.update(status="ok", price=base["price"], regime=rt["regime"], direction=rt["direction"],
                   adx=rt["adx"], er=rt["er"], entry_regime=re_["regime"], score=None)
        if sink is not None:
            sink[sym] = de.tail(120)

        pc = pair_ccys(sym)
        gap = None
        if pc and strength and pc[0] in strength and pc[1] in strength:
            gap = strength[pc[0]] - strength[pc[1]]
            row["strength_gap"] = round(gap)

        news_hit = nw.blocked(sym, nf) if nw else None
        if news_hit:
            row["news"] = news_hit
        alerts_ok = session_ok and not news_hit

        if mtf_on:
            dirs, mdir, mn = mtf.evaluate(sym, tfmaps, mtf_tfs, mt.get("mode", "direction"),
                                          mt.get("need", 0), cfg["regime"])
            row.update(mtf=dirs, mtf_match=mdir, mtf_n=mn)
            prevm = state["mtf"].get(sym, "__new__")
            state["mtf"][sym] = mdir or ""
            if alerts_ok and mt.get("notify") and mdir and prevm != "__new__" and prevm != mdir:
                events.append({**base, "kind": "match", "dir": mdir, "mtf": {t: d for t, d in dirs.items() if d}})

        if rt["regime"] != "TREND":
            if alerts_ok and prev and prev.startswith("TREND") and al.get("notify_trend_end"):
                events.append({**base, "kind": "end"})
            continue

        ranked.append(base)
        if alerts_ok and prev is not None and label != prev and al.get("notify_trend_start", True):
            events.append({**base, "kind": "flip" if prev.startswith("TREND") else "start"})

        if not al.get("notify_aligned_signals", True):
            continue
        if cfg.get("skip_volatile_entry", True) and re_["regime"] == "VOLATILE":
            continue

        want = "BUY" if rt["direction"] == "UP" else "SELL"
        items = []
        for name, scfg in cfg["strategies"].items():
            if not scfg.get("enabled", True):
                continue
            try:
                res = REGISTRY[name](de, scfg)
            except Exception as e:
                print(f"[scan] {sym} {name}: {e}")
                continue
            if res and res[1] == want:
                items.append(res)
        row["score"] = len(items)

        key = f"{sym}|{e_cfg['interval']}|{want}|{de.index[-1]}"
        if len(items) < cfg.get("min_confluence", 1) or key in state["seen"]:
            continue
        state["seen"][key] = 1            # marked even if blocked below, so a stale candle can't fire later

        block, notes = None, []
        if not session_ok:
            block = "session"
        elif news_hit:
            block = "news"
        elif mtf_on and mt.get("filter") and row.get("mtf_match") != rt["direction"]:
            block = "mtf"
        elif htf_on:
            dh = htf_data.get(sym)
            try:
                rh = classify(dh, cfg["regime"]) if dh is not None else None
            except Exception:
                rh = None
            if not rh or rh["regime"] != "TREND" or rh["direction"] != rt["direction"]:
                block = "htf"
            else:
                notes.append(f"{h_cfg['interval']} trend agrees")
        if not block and gap is not None:
            if ft.get("strength_filter") and ((want == "BUY" and gap <= 0) or (want == "SELL" and gap >= 0)):
                block = "strength"
            else:
                notes.append(f"{pc[0]} {strength[pc[0]]:+.0f} vs {pc[1]} {strength[pc[1]]:+.0f}")
        if not block and mtf_on and row.get("mtf_match") == rt["direction"]:
            notes.append("Timeframes agree: " + " · ".join(t for t in mtf_tfs if row["mtf"].get(t)))
        cd, cap = ft.get("cooldown") or {}, ft.get("daily_cap") or {}
        if not block and cd.get("enabled") and time.time() - lastsig.get(sym, 0) < cd.get("minutes", 5) * 60:
            block = "cooldown"
        if not block and cap.get("enabled") and daily["count"] >= cap.get("max", 20):
            block = "cap"
        if block:
            row["blocked"] = block
            continue

        lastsig[sym] = time.time()
        daily["count"] += 1
        events.append({**base, "kind": "signal", "dir": want, "items": items, "notes": notes})
        if tracker:
            entry_ts = de.index[-1]
            entry_ts = entry_ts.tz_localize("UTC") if entry_ts.tzinfo is None else entry_ts.tz_convert("UTC")
            tracker.add(ts=time.time(), sym=sym, name=display_name(sym), market=market, dir=want,
                        price=base["price"], entry_ts=entry_ts.isoformat(), adx=rt["adx"],
                        strats=[n for n, _, _ in items], hour=datetime.now(timezone.utc).hour)

    ranked.sort(key=lambda x: -x["r"]["adx"])
    signals = sorted([e for e in events if e["kind"] == "signal"], key=lambda x: -x["r"]["adx"])
    events = [e for e in events if e["kind"] != "signal"] + signals[: cfg.get("max_alerts_per_scan", 8)]

    extras = {"strength": strength, "session_open": session_ok, "resolved": resolved,
              "news_upcoming": nw.upcoming(nf)[:8] if nw else [], "news_error": nw.error if nw else None}
    return {"events": events, "ranked": ranked, "counts": counts, "state": state, "rows": rows, "extras": extras}


def summary_due(state, minutes):
    if not minutes:
        return False
    last = state.get("last_summary")
    return last is None or (time.time() - last) >= minutes * 60 - 30


def format_message(events, ranked, counts, cfg, with_summary, strength=None):
    al = cfg["alerts"]
    labels = al.get("labels", {"BUY": "BUY", "SELL": "SELL"})
    tf_t, tf_e = cfg["timeframes"]["trend"]["interval"], cfg["timeframes"]["entry"]["interval"]
    now = datetime.now(timezone.utc).strftime("%d %b %H:%M UTC")
    out = [f"📡 <b>Scalp Scanner</b> — {now}", ""]

    for e in events:
        r, name = e["r"], pretty(e["sym"])
        word = "uptrend" if r["direction"] == "UP" else "downtrend"
        tail = f"{name} [{e['market'].replace('_', ' ').upper()}] @ {e['price']:.5g}"
        if e["kind"] == "signal":
            icon = "🟢" if e["dir"] == "BUY" else "🔴"
            out.append(f"{icon} <b>{labels[e['dir']]}</b> · {tail}")
            out.append(f"   {tf_t} {word} (ADX {r['adx']:.0f}) → {tf_e} entry")
            for n, _, detail in e["items"]:
                out.append(f"   • {n}: {detail}")
            for note in e.get("notes", []):
                out.append(f"   ✔ {html.escape(note)}")
            if al.get("expiry_hint"):
                out.append(f"   ⏱ expiry idea: {al['expiry_hint']}")
        elif e["kind"] in ("start", "flip"):
            icon = "📈" if r["direction"] == "UP" else "📉"
            verb = "started" if e["kind"] == "start" else "reversed"
            out.append(f"{icon} <b>{tf_t} {word}</b> {verb}: {tail}")
            out.append(f"   ADX {r['adx']:.0f} · efficiency {r['er']:.2f}")
        elif e["kind"] == "match":
            up = e["dir"] == "UP"
            out.append(f"🧭 <b>Timeframes match {'UP' if up else 'DOWN'}</b>: {tail}")
            out.append("   " + " · ".join(f"{t}{'↑' if d == 'UP' else '↓'}" for t, d in e["mtf"].items()))
        elif e["kind"] == "end":
            out.append(f"⚪ {tf_t} trend faded: {tail}")
        out.append("")

    if with_summary:
        if ranked:
            out.append(f"<b>Trending now ({tf_t}), strongest first</b>")
            for t in ranked[:12]:
                arrow = "↑" if t["r"]["direction"] == "UP" else "↓"
                out.append(f"{arrow} {pretty(t['sym'])}  ADX {t['r']['adx']:.0f}")
        else:
            out.append("😴 No trending markets right now.")
        mix = " · ".join(f"{counts.get(k, 0)} {k.lower()}" for k in ("TREND", "RANGE", "FLAT", "VOLATILE"))
        out += ["", f"Market mix: {mix}"]
        if strength:
            order = sorted(strength.items(), key=lambda kv: -kv[1])
            out.append("💪 Strongest: " + ", ".join(f"{c} {v:+.0f}" for c, v in order[:2])
                       + "  ·  Weakest: " + ", ".join(f"{c} {v:+.0f}" for c, v in order[-2:][::-1]))
    return "\n".join(out)


def run_once(cfg, notify=True, sink=None):
    res = scan(cfg, sink)
    events, ranked, counts, state = res["events"], res["ranked"], res["counts"], res["state"]
    ft = cfg.get("features") or {}
    due = summary_due(state, cfg["alerts"].get("summary_every_minutes", 0))
    sent = False
    if notify and (events or due):
        strength = res["extras"]["strength"] if ft.get("strength_meter") else None
        sent = bool(send(format_message(events, ranked, counts, cfg, due, strength)))
        if due and sent:                      # only start the summary timer if Telegram really accepted it
            state["last_summary"] = time.time()
    elif not events:
        print("No alerts this scan.")
    save_state(state)
    return {"events": events, "ranked": ranked, "counts": dict(counts), "rows": res["rows"],
            "extras": res["extras"], "sent": sent}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--test", action="store_true", help="send a test Telegram message")
    p.add_argument("--loop", type=int, default=0, metavar="SEC",
                   help="keep running, scanning every SEC seconds (use on your own PC)")
    a = p.parse_args()
    load_env()

    if a.test:
        send("✅ Scalp Scanner connected to Telegram.")
        return

    with open(a.config) as f:
        cfg = yaml.safe_load(f)

    if not a.loop:
        run_once(cfg)
        return
    while True:
        t0 = time.time()
        try:
            run_once(cfg)
        except Exception as e:
            print(f"[loop] scan failed: {e}")
        time.sleep(max(5, a.loop - (time.time() - t0)))


if __name__ == "__main__":
    main()
