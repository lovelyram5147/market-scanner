"""Chooses where each pair's candles come from: Twelve Data (key pool) first, Yahoo for the rest."""
from . import mtf as MT
from . import twelvedata as td


def _merge(tfmaps, got, tfs):
    ok = set()
    for sym, df1m in got.items():
        frames = td.derive(df1m, tfs)
        if len(frames.get(tfs[0], [])) < 60:
            continue
        for tf, d in frames.items():
            if len(d) >= 60:
                tfmaps[tf][sym] = d
        ok.add(sym)
    return ok


def load(cfg, symbols, tfs, state, now=None):
    """Returns ({tf: {symbol: DataFrame}}, info)."""
    tdc = cfg.get("twelvedata") or {}
    keys = td.keys_from_env() if tdc.get("enabled") else []
    tfmaps = {tf: {} for tf in tfs}
    info = {"td": 0, "yahoo": 0, "keys": len(keys), "left": None, "budget": 0}
    pool, done = None, set()
    if keys:
        pool = td.Pool(keys, tdc, state, now)
        want = pool.choose(symbols, set((state.get("align") or {}).keys()))
        info["budget"] = len(want)
        done = _merge(tfmaps, pool.fetch(want), tfs)
        print(f"[td] {len(keys)} key(s) · budget {info['budget']} · got {len(done)}")
    rest = [s for s in symbols if s not in done]
    if rest:
        yf = MT.build(rest, tfs, {})
        for tf in tfs:
            tfmaps[tf].update(yf.get(tf, {}))
    if pool:
        # anything Yahoo could not give us: try Twelve Data again with whatever credits are left (reserve included)
        missing = [s for s in rest if s not in tfmaps[tfs[0]]]
        if missing:
            pool.cfg = dict(pool.cfg, reserve_pct=0)
            extra = pool.fetch(pool.eligible(missing)[: pool.budget() or len(missing)])
            done |= _merge(tfmaps, extra, tfs)
        info["left"] = pool.credits_left()
    info["td"] = len(done)
    info["yahoo"] = sum(1 for s in symbols if s not in done and s in tfmaps[tfs[0]])
    return tfmaps, info
