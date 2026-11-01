import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone

import yaml

from .data import fetch
from .notifier import send
from .regime import classify
from .strategies import REGISTRY

STATE_FILE = "state.json"


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
    state["seen"] = dict(list(state["seen"].items())[-2000:])
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def scan(cfg):
    state = load_state()
    al = cfg["alerts"]
    events, trending = [], []

    for market, m in cfg["markets"].items():
        for sym in m["symbols"]:
            df = fetch(sym, m["interval"], m["period"])
            if df is None:
                print(f"[scan] no data for {sym}")
                continue
            try:
                r = classify(df, cfg["regime"])
            except Exception as e:
                print(f"[scan] {sym} regime error: {e}")
                continue

            label = f"TREND_{r['direction']}" if r["regime"] == "TREND" else r["regime"]
            rkey = f"{sym}|{m['interval']}"
            prev = state["regime"].get(rkey)
            state["regime"][rkey] = label
            print(f"[regime] {sym:12} {m['interval']:4} {label:11} ADX {r['adx']:.0f}  ER {r['er']:.2f}")

            price = float(df["Close"].iloc[-1])
            base = dict(market=market, sym=sym, tf=m["interval"], price=price, r=r)

            if r["regime"] != "TREND":
                if prev and prev.startswith("TREND") and al.get("notify_trend_end"):
                    events.append({**base, "kind": "end", "prev": prev})
                continue

            trending.append(base)
            if label != prev and al.get("notify_trend_start", True):
                kind = "flip" if prev and prev.startswith("TREND") else "start"
                events.append({**base, "kind": kind})

            if al.get("notify_aligned_signals", True):
                want = "BUY" if r["direction"] == "UP" else "SELL"
                bar_ts = str(df.index[-1])
                items = []
                for name, scfg in cfg["strategies"].items():
                    if not scfg.get("enabled", True):
                        continue
                    try:
                        res = REGISTRY[name](df, scfg)
                    except Exception as e:
                        print(f"[scan] {sym} {name}: {e}")
                        continue
                    if res and res[1] == want:
                        items.append(res)
                key = f"{sym}|{m['interval']}|{want}|{bar_ts}"
                if len(items) >= cfg.get("min_confluence", 1) and key not in state["seen"]:
                    state["seen"][key] = 1
                    events.append({**base, "kind": "signal", "dir": want, "items": items})

    save_state(state)
    return events, trending


def fmt_line(e):
    return f"{e['sym']} [{e['market'].upper()} · {e['tf']}] @ {e['price']:.5g}"


def format_message(events, trending, cfg):
    now = datetime.now(timezone.utc).strftime("%d %b %H:%M UTC")
    out = [f"📡 <b>Trend Scanner</b> — {now}", ""]
    for e in events:
        r = e["r"]
        stats = f"ADX {r['adx']:.0f} · efficiency {r['er']:.2f}"
        if e["kind"] in ("start", "flip"):
            icon, word = ("📈", "UPTREND") if r["direction"] == "UP" else ("📉", "DOWNTREND")
            verb = "started" if e["kind"] == "start" else "reversed into"
            out.append(f"{icon} <b>{word}</b> {verb}: {fmt_line(e)}")
            out.append(f"   {stats}")
        elif e["kind"] == "signal":
            icon = "🟢" if e["dir"] == "BUY" else "🔴"
            out.append(f"{icon} <b>{e['dir']}</b> (with trend): {fmt_line(e)}")
            for name, _, detail in e["items"]:
                out.append(f"   • {name}: {detail}")
        elif e["kind"] == "end":
            out.append(f"⚪ Trend faded ({e['r']['regime']}): {fmt_line(e)}")
        out.append("")
    if cfg["alerts"].get("summary_every_scan") and trending:
        out.append("<b>Currently trending</b>")
        for t in trending:
            arrow = "↑" if t["r"]["direction"] == "UP" else "↓"
            out.append(f"{arrow} {t['sym']} ({t['tf']}) ADX {t['r']['adx']:.0f}")
    return "\n".join(out)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--test", action="store_true", help="send a test Telegram message")
    a = p.parse_args()

    if a.test:
        send("✅ Trend Scanner connected to Telegram.")
        return

    with open(a.config) as f:
        cfg = yaml.safe_load(f)
    events, trending = scan(cfg)
    send_summary = cfg["alerts"].get("summary_every_scan") and trending
    if events or send_summary:
        send(format_message(events, trending, cfg))
    else:
        print("No trend updates.")


if __name__ == "__main__":
    main()
