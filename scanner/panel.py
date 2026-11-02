"""Local control panel: python -m scanner.panel  ->  http://127.0.0.1:8765"""
import argparse
import copy
import json
import math
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from . import main as M
from . import tracker as trk
from .indicators import ema
from .notifier import send

SETTINGS_FILE = "panel_settings.json"
HTML_FILE = Path(__file__).with_name("panel.html")
SESSION_NAMES = ["asia", "london", "newyork"]

# key -> ("bool",) | ("int"|"float", lo, hi) | ("list", allowed or None)
SCHEMA = {
    "scan_seconds": ("int", 20, 900), "min_confluence": ("int", 1, 5), "trend_adx": ("float", 15, 45),
    "summary_minutes": ("int", 0, 180), "cooldown_minutes": ("int", 1, 60), "cap_max": ("int", 1, 500),
    "news_before": ("int", 0, 120), "news_after": ("int", 0, 120),
    "telegram_on": ("bool",), "notify_trend_start": ("bool",), "notify_signals": ("bool",),
    "skip_volatile_entry": ("bool",), "htf_confirm": ("bool",), "strength_meter": ("bool",),
    "strength_filter": ("bool",), "cooldown_on": ("bool",), "cap_on": ("bool",), "sessions_on": ("bool",),
    "news_on": ("bool",), "tracker": ("bool",), "mini_chart": ("bool",), "candle_countdown": ("bool",),
    "sound_alert": ("bool",), "voice_alert": ("bool",), "popup_alert": ("bool",), "tray_icon": ("bool",),
    "mtf_on": ("bool",), "mtf_filter": ("bool",), "mtf_notify": ("bool",), "mtf_need": ("int", 0, 6),
    "mtf_mode": ("choice", ["direction", "trend"]), "mtf_tfs": ("list", ["1m", "5m", "10m", "15m", "30m", "1h"]),
    "groups": ("list", None), "sessions": ("list", SESSION_NAMES), "muted": ("list", None),
}


def clean(o):
    """Make data JSON-safe (NaN/inf -> None)."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    return o


def write_env(token, chat, path=".env"):
    lines = []
    if os.path.exists(path):
        lines = [l.rstrip("\n") for l in open(path, encoding="utf-8")
                 if not l.startswith(("TELEGRAM_BOT_TOKEN=", "TELEGRAM_CHAT_ID="))]
    lines += [f"TELEGRAM_BOT_TOKEN={token}", f"TELEGRAM_CHAT_ID={chat}"]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"] = token, chat


# ---- start with Windows (a small .bat in the Startup folder)
def startup_file():
    if sys.platform != "win32" or not os.environ.get("APPDATA"):
        return None
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup/ScalpScanner.bat"


def autostart_state():
    p = startup_file()
    return None if p is None else p.exists()


def set_autostart(on):
    p = startup_file()
    if p is None:
        return False
    if on:
        exe = Path(sys.executable)
        pyw = exe.with_name("pythonw.exe")
        runner = pyw if pyw.exists() else exe
        root = Path(__file__).resolve().parent.parent
        p.write_text(f'@echo off\ncd /d "{root}"\nstart "" /min "{runner}" -m scanner.panel --no-browser\n')
    elif p.exists():
        p.unlink()
    return True


class Engine:
    def __init__(self, config_path, autostart=True):
        with open(config_path) as f:
            self.base = yaml.safe_load(f)
        b = self.base
        ft = b.get("features") or {}
        cd, cap = ft.get("cooldown") or {}, ft.get("daily_cap") or {}
        se, nw, mt = ft.get("sessions") or {}, ft.get("news") or {}, ft.get("mtf") or {}
        self.settings = {
            "scan_seconds": 60, "telegram_on": True,
            "min_confluence": b.get("min_confluence", 2), "trend_adx": b["regime"]["trend_adx"],
            "notify_trend_start": b["alerts"].get("notify_trend_start", True),
            "notify_signals": b["alerts"].get("notify_aligned_signals", True),
            "summary_minutes": b["alerts"].get("summary_every_minutes", 15),
            "skip_volatile_entry": b.get("skip_volatile_entry", True),
            "groups": list(b["markets"].keys()),
            "htf_confirm": bool(ft.get("htf_confirm", False)),
            "strength_meter": bool(ft.get("strength_meter", True)),
            "strength_filter": bool(ft.get("strength_filter", False)),
            "cooldown_on": bool(cd.get("enabled", True)), "cooldown_minutes": cd.get("minutes", 5),
            "cap_on": bool(cap.get("enabled", False)), "cap_max": cap.get("max", 20),
            "sessions_on": bool(se.get("enabled", False)), "sessions": se.get("active", list(SESSION_NAMES)),
            "news_on": bool(nw.get("enabled", False)), "news_before": nw.get("before", 15),
            "news_after": nw.get("after", 15),
            "tracker": bool(ft.get("tracker", True)), "mini_chart": True, "candle_countdown": True,
            "sound_alert": True, "voice_alert": False, "popup_alert": False, "tray_icon": False,
            "muted": list(b.get("muted") or []),
            "mtf_on": bool(mt.get("enabled", True)), "mtf_tfs": mt.get("timeframes", ["1m", "5m", "15m"]),
            "mtf_mode": mt.get("mode", "direction"), "mtf_need": mt.get("need", 0),
            "mtf_filter": bool(mt.get("filter", False)), "mtf_notify": bool(mt.get("notify", False)),
        }
        try:
            self.update_settings(json.load(open(SETTINGS_FILE)), save=False)
        except Exception:
            pass
        self.lock = threading.Lock()
        self.running, self.force, self.busy = autostart, False, False
        self.next_run = 0.0
        self.rows, self.signals, self.last_signal, self.candles = [], [], {}, {}
        self.counts, self.extras = {}, {}
        self.last_scan, self.scan_secs, self.last_error = None, None, None
        self.sig_id, self.stop = 0, False

    # ---- config with panel settings applied
    def cfg(self):
        c, s = copy.deepcopy(self.base), self.settings
        c["min_confluence"] = int(s["min_confluence"])
        c["regime"]["trend_adx"] = float(s["trend_adx"])
        c["skip_volatile_entry"] = bool(s["skip_volatile_entry"])
        c["alerts"].update(notify_trend_start=bool(s["notify_trend_start"]),
                           notify_aligned_signals=bool(s["notify_signals"]),
                           summary_every_minutes=int(s["summary_minutes"]))
        c["markets"] = {g: l for g, l in c["markets"].items() if g in s["groups"]}
        f = c.setdefault("features", {})
        old_news = f.get("news") or {}
        f.update(tracker=s["tracker"], strength_meter=s["strength_meter"],
                 strength_filter=s["strength_filter"], htf_confirm=s["htf_confirm"],
                 cooldown={"enabled": s["cooldown_on"], "minutes": s["cooldown_minutes"]},
                 daily_cap={"enabled": s["cap_on"], "max": s["cap_max"]},
                 sessions={"enabled": s["sessions_on"], "active": s["sessions"]},
                 news={"enabled": s["news_on"], "impact": old_news.get("impact", "High"),
                       "before": s["news_before"], "after": s["news_after"]})
        f["mtf"] = {"enabled": s["mtf_on"], "timeframes": s["mtf_tfs"], "mode": s["mtf_mode"],
                    "need": s["mtf_need"], "filter": s["mtf_filter"], "notify": s["mtf_notify"]}
        c["muted"] = s["muted"]
        return c

    def telegram_ready(self):
        return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))

    def scan(self):
        self.busy = True
        t0 = time.time()
        try:
            cfg = self.cfg()
            sink = {} if self.settings["mini_chart"] else None
            res = M.run_once(cfg, notify=bool(self.settings["telegram_on"]) and self.telegram_ready(), sink=sink)
            with self.lock:
                self.rows, self.counts, self.extras, self.last_error = res["rows"], res["counts"], res["extras"], None
                if sink is not None:
                    self.candles = sink
                for e in res["events"]:
                    self.sig_id += 1
                    item = {"id": self.sig_id, "ts": time.time(), "kind": e["kind"], "sym": e["sym"],
                            "name": M.display_name(e["sym"]), "market": e["market"], "price": e["price"],
                            "adx": e["r"]["adx"], "direction": e["r"]["direction"], "dir": e.get("dir"),
                            "reasons": [f"{n}: {d}" for n, _, d in e.get("items", [])],
                            "notes": e.get("notes", []), "match": e.get("dir") if e["kind"] == "match" else None,
                            "tfs": e.get("mtf")}
                    self.signals.insert(0, item)
                    if e["kind"] == "signal":
                        self.last_signal[e["sym"]] = {"dir": e["dir"], "ts": item["ts"]}
                self.signals = self.signals[:150]
        except Exception as ex:
            self.last_error = f"{type(ex).__name__}: {ex}"
            print("[panel] scan failed:", self.last_error)
        finally:
            self.scan_secs = time.time() - t0
            self.last_scan = time.time()
            self.next_run = time.time() + int(self.settings["scan_seconds"])
            self.busy = False

    def loop(self):
        while not self.stop:
            if self.force or (self.running and time.time() >= self.next_run):
                self.force = False
                self.scan()
            time.sleep(0.5)

    def state(self):
        with self.lock:
            rows = [dict(r, last_signal=self.last_signal.get(r["sym"])) for r in self.rows]
            cfg = self.cfg()
            ex = self.extras or {}
            strength = sorted(({"ccy": c, "v": v} for c, v in (ex.get("strength") or {}).items()),
                              key=lambda x: -x["v"])
            return clean({
                "running": self.running, "busy": self.busy, "now": time.time(),
                "last_scan": self.last_scan, "scan_secs": self.scan_secs, "error": self.last_error,
                "next_in": max(0, self.next_run - time.time()) if self.running else None,
                "rows": rows, "signals": self.signals[:60], "counts": self.counts,
                "settings": self.settings, "all_groups": list(self.base["markets"].keys()),
                "timeframes": {k: v["interval"] for k, v in self.base["timeframes"].items()},
                "n_strategies": sum(1 for v in cfg["strategies"].values() if v.get("enabled", True)),
                "telegram_ready": self.telegram_ready(), "strength": strength,
                "session_open": ex.get("session_open", True), "news": ex.get("news_upcoming", []),
                "news_error": ex.get("news_error"), "autostart": autostart_state(),
            })

    def chart(self, sym):
        with self.lock:
            df = self.candles.get(sym)
            if df is None or df.empty:
                return {"error": "No candles yet. Wait for the next scan."}
            df = df.tail(100)
            idx = df.index.tz_localize("UTC") if df.index.tz is None else df.index.tz_convert("UTC")
            t = [int(x.timestamp()) for x in idx]
            row = next((r for r in self.rows if r["sym"] == sym), {})
            marks = trk.get().marks(sym, time.time() - 6 * 3600) if self.settings["tracker"] else []
            return clean({"sym": sym, "name": M.display_name(sym), "interval": self.base["timeframes"]["entry"]["interval"],
                          "t": t, "o": df["Open"].round(6).tolist(), "h": df["High"].round(6).tolist(),
                          "l": df["Low"].round(6).tolist(), "c": df["Close"].round(6).tolist(),
                          "ema": ema(df["Close"], 21).round(6).tolist(), "marks": marks,
                          "regime": row.get("regime"), "direction": row.get("direction")})

    def update_settings(self, new, save=True):
        for k, v in new.items():
            sch = SCHEMA.get(k)
            if not sch or k not in self.settings:
                continue
            kind = sch[0]
            if kind == "bool":
                v = bool(v)
            elif kind == "choice":
                v = v if v in sch[1] else self.settings[k]
            elif kind in ("int", "float"):
                v = max(sch[1], min(sch[2], float(v)))
                v = int(v) if kind == "int" else v
            else:
                allowed = sch[1] or (list(self.base["markets"].keys()) if k == "groups" else None)
                v = [x for x in v if allowed is None or x in allowed]
                if k == "mtf_tfs":
                    v = [x for x in sch[1] if x in v] or self.settings[k]      # keep order, never empty
            self.settings[k] = v
        if save:
            json.dump(self.settings, open(SETTINGS_FILE, "w"))


def make_handler(engine, port):
    ok_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _host_ok(self):
            return self.headers.get("Host", "") in ok_hosts

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, "{}")
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/api/state":
                return self._send(200, json.dumps(engine.state()))
            if u.path == "/api/stats":
                try:
                    k, payout = int(q.get("k", ["1"])[0]), float(q.get("payout", ["85"])[0])
                except ValueError:
                    return self._send(400, "{}")
                payout = max(10.0, min(200.0, payout))
                return self._send(200, json.dumps(clean(trk.get().stats(k, payout))))
            if u.path == "/api/chart":
                return self._send(200, json.dumps(engine.chart(q.get("sym", [""])[0])))
            if u.path in ("/", "/index.html"):
                return self._send(200, HTML_FILE.read_bytes(), "text/html; charset=utf-8")
            self._send(404, "{}")

        def do_POST(self):
            # custom header blocks cross-site form posts; host check blocks DNS rebinding
            if not self._host_ok() or self.headers.get("X-Panel") != "1":
                return self._send(403, "{}")
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            except Exception:
                return self._send(400, "{}")
            p = self.path
            if p == "/api/control":
                a = body.get("action")
                if a == "start":
                    engine.running, engine.next_run = True, 0
                elif a == "stop":
                    engine.running = False
                elif a == "scan":
                    engine.force = True
            elif p == "/api/settings":
                engine.update_settings(body)
            elif p == "/api/mute":
                sym, on = str(body.get("sym", "")), bool(body.get("muted"))
                known = {s for lst in engine.base["markets"].values() for s in lst}
                if sym in known:
                    m = [x for x in engine.settings["muted"] if x != sym] + ([sym] if on else [])
                    engine.update_settings({"muted": m})
                    engine.force = True
            elif p == "/api/autostart":
                if not set_autostart(bool(body.get("enabled"))):
                    return self._send(200, json.dumps({"ok": False, "msg": "Start with Windows only works on Windows."}))
            elif p == "/api/telegram":
                t, c = str(body.get("token", "")).strip(), str(body.get("chat_id", "")).strip()
                if not t or not c:
                    return self._send(400, json.dumps({"ok": False, "msg": "Token and chat ID are both required."}))
                write_env(t, c)
            elif p == "/api/telegram/test":
                if not engine.telegram_ready():
                    return self._send(200, json.dumps({"ok": False, "msg": "Save token and chat ID first."}))
                ok = send("✅ Scalp Scanner panel connected to Telegram.")
                return self._send(200, json.dumps({"ok": ok, "msg": "Sent." if ok else "Telegram refused it. Check token, chat ID, and that the bot is an admin."}))
            else:
                return self._send(404, "{}")
            self._send(200, json.dumps({"ok": True}))

    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--paused", action="store_true", help="start with scanning paused")
    a = ap.parse_args()
    M.load_env()
    eng = Engine(a.config, autostart=not a.paused)
    threading.Thread(target=eng.loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(eng, a.port))
    url = f"http://127.0.0.1:{a.port}"
    print(f"Panel running at {url}  (Ctrl+C to quit)")
    if eng.settings["tray_icon"]:
        from . import tray
        tray.start(eng, url, lambda: threading.Thread(target=srv.shutdown, daemon=True).start())
    if not a.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    eng.stop = True


if __name__ == "__main__":
    main()
