"""GitHub mode: report only pairs where several timeframes point the same way.

Ranking:  1) 3 timeframes agree   2) 2 timeframes agree   3) ADX (tie-break only)
Run:      python -m scanner.align --config config.github.yaml
"""
import argparse
import os
import time
from datetime import datetime, timezone

import yaml

from . import datasource
from . import mtf as MT
from .data import MINUTES, age_minutes
from .indicators import adx as adx_fn
from .main import display_name, load_env, load_state, pretty, save_state
from .notifier import send

TIER_TITLES = {1: "🥇 <b>3 or more timeframes agree</b>", 2: "🥈 <b>2 timeframes agree</b>"}


def best_alignment(dirs, adxs, tiers, order):
    """Pure logic. dirs/adxs are {timeframe: 'UP'|'DOWN'|None / float}. Returns None or a result dict."""
    for tier, key in ((1, "best"), (2, "second")):
        found = []
        for grp in tiers.get(key, []):
            ds = [dirs.get(t) for t in grp]
            if ds and ds[0] in ("UP", "DOWN") and all(d == ds[0] for d in ds):
                found.append((ds[0], grp))
        if not found:
            continue
        by_dir = {}
        for d, grp in found:                          # several chains in one direction merge, e.g. 1-5-15 + 5-15-30
            by_dir.setdefault(d, set()).update(grp)
        # if (rarely) both directions qualify, keep the one with more timeframes, then more chains
        direction = max(by_dir, key=lambda d: (len(by_dir[d]), sum(1 for x, _ in found if x == d)))
        tfs = [t for t in order if t in by_dir[direction]]
        vals = [adxs[t] for t in tfs if adxs.get(t) is not None]
        return {"tier": tier, "dir": direction, "tfs": tfs, "n": len(tfs),
                "adx": sum(vals) / len(vals) if vals else 0.0}
    return None


def analyse(cfg, tfmaps):
    tfs = cfg["timeframes"]
    order = [t for t in MT.ORDER if t in tfs]
    smallest = order[0]
    results, closed, nodata = [], 0, 0
    for market, lst in cfg["markets"].items():
        for sym in lst:
            if sym in set(cfg.get("muted") or []):
                continue
            base = tfmaps.get(smallest, {}).get(sym)
            if base is None:
                nodata += 1
                continue
            limit = max(cfg.get("fresh_minutes", 30), 2 * MINUTES.get(smallest, 5))
            if age_minutes(base) > limit:             # weekend / session closed / stale data
                closed += 1
                continue
            dirs, adxs = {}, {}
            for t in order:
                df = tfmaps.get(t, {}).get(sym)
                try:
                    dirs[t] = MT.direction(df, cfg.get("mode", "direction"), cfg["regime"])
                    a = float(adx_fn(df, 14)[0].iloc[-1]) if df is not None and len(df) >= 60 else None
                    adxs[t] = a if a == a else None   # NaN -> None
                except Exception:
                    dirs[t], adxs[t] = None, None
            res = best_alignment(dirs, adxs, cfg["tiers"], order)
            if res and res["adx"] >= cfg.get("min_adx", 0):
                res.update(sym=sym, name=display_name(sym), market=market,
                           price=float(base["Close"].iloc[-1]), dirs=dirs)
                results.append(res)
    # preference order: tier, then how many timeframes agree, then ADX (last)
    results.sort(key=lambda r: (r["tier"], -r["n"], -r["adx"]))
    return results, closed, nodata


def tag_changes(results, prev):
    for r in results:
        p = prev.get(r["sym"])
        if not p or p["dir"] != r["dir"]:
            r["tag"] = "🆕"
        elif r["tier"] < p["tier"]:
            r["tag"] = "⬆"
        else:
            r["tag"] = ""
    lost = [s for s in prev if s not in {r["sym"] for r in results}]
    return lost


def format_message(results, closed, scanned, cfg, lost=(), info=None):
    labels = cfg.get("labels", {"UP": "UP", "DOWN": "DOWN"})
    now = datetime.now(timezone.utc).strftime("%d %b %H:%M UTC")
    order = [t for t in MT.ORDER if t in cfg["timeframes"]]
    out = [f"🧭 <b>Timeframe alignment</b> — {now}"]
    if not results:
        out += ["", "😴 No pair has 2 or more timeframes pointing the same way right now."]
    cap = cfg.get("max_per_tier", 15)
    for tier in (1, 2):
        grp = [r for r in results if r["tier"] == tier]
        if not grp:
            continue
        out += ["", TIER_TITLES[tier]]
        for r in grp[:cap]:
            up = r["dir"] == "UP"
            out.append(f"{'🟢' if up else '🔴'} <b>{pretty(r['sym'])}</b> {labels[r['dir']]} {'▲' if up else '▼'}"
                       f"  {'·'.join(r['tfs'])}  ADX {r['adx']:.0f} {r['tag']}".rstrip())
            out.append("    " + " ".join(f"{t}{'▲' if r['dirs'].get(t) == 'UP' else '▼' if r['dirs'].get(t) == 'DOWN' else '·'}"
                                         for t in order))
        if len(grp) > cap:
            out.append(f"   … and {len(grp) - cap} more")
    if lost and cfg.get("notify_lost"):
        out += ["", "⚪ No longer aligned: " + ", ".join(pretty(s) for s in lost)]
    n1 = sum(1 for r in results if r["tier"] == 1)
    out += ["", f"Scanned {scanned} · 3+ TF {n1} · 2 TF {len(results) - n1} · closed {closed}"]
    if info and info.get("keys") and cfg.get("show_source", True):
        left = f" · ≈{info['left']:,} credits left today" if info.get("left") is not None else ""
        out.append(f"Data: Twelve Data {info['td']} · Yahoo {info['yahoo']}{left}")
    return "\n".join(out)


def run(cfg):
    state = load_state()
    prev = state.setdefault("align", {})
    muted = set(cfg.get("muted") or [])
    symbols = [s for lst in cfg["markets"].values() for s in lst if s not in muted]
    order = [t for t in MT.ORDER if t in cfg["timeframes"]]
    tfmaps, info = datasource.load(cfg, symbols, order, state)

    if not any(tfmaps.get(t) for t in tfmaps):        # nothing downloaded at all
        print("[align] no data from Yahoo this run")
        if cfg.get("warn_on_fail", True) and time.time() - state.get("last_warn", 0) > 3600:
            if send("⚠️ Timeframe scanner could not download market data this run. It will keep retrying."):
                state["last_warn"] = time.time()
        save_state(state)
        return

    results, closed, nodata = analyse(cfg, tfmaps)
    lost = tag_changes(results, prev)
    for r in results:
        print(f"[align] T{r['tier']} {r['name']:9} {r['dir']:4} {'·'.join(r['tfs']):16} ADX {r['adx']:.0f} {r['tag']}")
    force = os.getenv("FORCE_SEND", "").lower() == "true"
    changed = any(r["tag"] for r in results) or (lost and cfg.get("notify_lost"))
    should = (bool(results) or cfg.get("send_when_empty", False) or force) and \
             (not cfg.get("only_changes", False) or changed or force)
    if should:
        send(format_message(results, closed, len(symbols) - nodata, cfg, lost, info))
    else:
        print("[align] nothing to send this run")
    state["align"] = {r["sym"]: {"tier": r["tier"], "dir": r["dir"], "n": r["n"]} for r in results}
    save_state(state)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.github.yaml")
    p.add_argument("--test", action="store_true", help="send a test Telegram message")
    a = p.parse_args()
    load_env()
    if a.test:
        send("✅ Timeframe scanner connected to Telegram.")
        return
    with open(a.config) as f:
        run(yaml.safe_load(f))


if __name__ == "__main__":
    main()
