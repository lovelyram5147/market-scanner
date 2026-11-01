import argparse
import html
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone

import yaml

from .data import fetch_many, is_fresh
from .notifier import send
from .regime import classify
from .strategies import REGISTRY

STATE_FILE = "state.json"
NAMES = {"^NSEI": "NIFTY 50", "^NSEBANK": "BANK NIFTY", "^GSPC": "S&P 500", "GC=F": "GOLD"}


def pretty(sym):
    return html.escape(NAMES.get(sym, sym.replace("=X", "")))


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
            return s
    except Exception:
        pass
    return {"seen": {}, "regime": {}}


def save_state(state):
    state["seen"] = dict(list(state["seen"].items())[-3000:])
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def scan(cfg):
    state = load_state()
    al = cfg["alerts"]
    t_cfg, e_cfg = cfg["timeframes"]["trend"], cfg["timeframes"]["entry"]
    pairs = [(g, s) for g, lst in cfg["markets"].items() for s in lst]
    symbols = [s for _, s in pairs]

    trend_data = fetch_many(symbols, t_cfg["interval"], t_cfg["period"])
    entry_data = fetch_many(symbols, e_cfg["interval"], e_cfg["period"])

    events, ranked, counts = [], [], defaultdict(int)
    for market, sym in pairs:
        dt, de = trend_data.get(sym), entry_data.get(sym)
        if dt is None or de is None:
            print(f"[scan] no data: {sym}")
            continue
        if not is_fresh(de, e_cfg["interval"], cfg.get("max_age_bars", 5)):
            print(f"[scan] stale/closed: {sym}")
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
        if rt["regime"] != "TREND":
            if prev and prev.startswith("TREND") and al.get("notify_trend_end"):
                events.append({**base, "kind": "end"})
            continue

        ranked.append(base)
        # first time we see a pair we only record it (it shows in the summary) instead of announcing a "start"
        if prev is not None and label != prev and al.get("notify_trend_start", True):
            kind = "flip" if prev and prev.startswith("TREND") else "start"
            events.append({**base, "kind": kind})

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
        key = f"{sym}|{e_cfg['interval']}|{want}|{de.index[-1]}"
        if len(items) >= cfg.get("min_confluence", 1) and key not in state["seen"]:
            state["seen"][key] = 1
            events.append({**base, "kind": "signal", "dir": want, "items": items})

    # strongest trends first; cap the number of signal alerts per scan
    ranked.sort(key=lambda x: -x["r"]["adx"])
    signals = sorted([e for e in events if e["kind"] == "signal"], key=lambda x: -x["r"]["adx"])
    keep = signals[: cfg.get("max_alerts_per_scan", 8)]
    events = [e for e in events if e["kind"] != "signal"] + keep
    return events, ranked, counts, state


def summary_due(state, minutes):
    if not minutes:
        return False
    last = state.get("last_summary")
    return last is None or (time.time() - last) >= minutes * 60 - 30


def format_message(events, ranked, counts, cfg, with_summary):
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
            if al.get("expiry_hint"):
                out.append(f"   ⏱ expiry idea: {al['expiry_hint']}")
        elif e["kind"] in ("start", "flip"):
            icon = "📈" if r["direction"] == "UP" else "📉"
            verb = "started" if e["kind"] == "start" else "reversed"
            out.append(f"{icon} <b>{tf_t} {word}</b> {verb}: {tail}")
            out.append(f"   ADX {r['adx']:.0f} · efficiency {r['er']:.2f}")
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
    return "\n".join(out)


def run_once(cfg):
    events, ranked, counts, state = scan(cfg)
    due = summary_due(state, cfg["alerts"].get("summary_every_minutes", 0))
    if events or due:
        send(format_message(events, ranked, counts, cfg, due))
        if due:
            state["last_summary"] = time.time()
    else:
        print("No alerts this scan.")
    save_state(state)


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
