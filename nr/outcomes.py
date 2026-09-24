"""Paper entries, liquidity tracking and outcome evaluation.

Rules enforced here:
- Every candidate (researched or not) is entered at T_D with the same cost
  model, so all arms share one entry.
- Entry reference is the WORSE of the live quote at T_D and the next 1-min
  bar's open. Fees, CPMM price impact, an MEV buffer and tx costs are charged
  on both sides.
- Exit at a horizon uses the pool liquidity observed at that time. Below the
  exit-liquidity floor, or if the pair is gone, the position is worth 0.
- Barrier outcomes are strictly sequential on 1-min bars. On the fill bar only
  the adverse side counts. If target and stop share a bar, it's a loss.
"""
import json
import math
from datetime import timedelta

from . import db, sources
from .config import PREREG, PREREG_VERSION

P = PREREG


# ------------------------------------------------------------------ entry
def take_entry(c: dict):
    q = sources.pair_now(c["pair_address"])
    now = db.iso(db.now_utc())
    if not q:
        db.conn().execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?)",
                          (c["id"], now, None, None, None, None, 0, "no quote at T_D"))
        db.log("warn", f"entry #{c['id']} no quote (pair missing) -> treated as unexitable")
        return
    price = sources.f(q.get("priceUsd"))
    liq = (q.get("liquidity") or {}).get("usd")
    mcap = q.get("marketCap") or q.get("fdv")
    slim = {k: q.get(k) for k in ("priceUsd", "liquidity", "marketCap", "fdv", "volume",
                                  "txns", "priceChange")}
    db.conn().execute("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?)",
                      (c["id"], now, price, liq, mcap, json.dumps(slim), 1, None))
    db.ledger("entry", {"candidate_id": c["id"], "t5": now, "price": price, "liq": liq})
    db.log("info", f"entry #{c['id']} {c.get('symbol')} @ {price} liq=${liq or 0:,.0f}")
    reps = {r["arm"]: r for r in db.conn().execute(
        "SELECT arm, report_json, late FROM reports WHERE candidate_id=? AND ok=1", (c["id"],))}
    if "C" in reps and not reps["C"]["late"]:
        from . import notify
        rep = json.loads(reps["C"]["report_json"])
        if notify.is_trade(rep) or notify._all():
            sk = json.loads(reps["S"]["report_json"]) if "S" in reps else None
            notify.entry(c, rep, sk, price, liq, mcap)


def snapshot_liquidity(cands: list[dict]):
    """One liquidity/price observation for each open candidate (batched)."""
    now = db.iso(db.now_utc())
    by_pair = {c["pair_address"]: c for c in cands}
    pairs = list(by_pair)
    for i in range(0, len(pairs), 30):
        chunk = pairs[i:i + 30]
        d = sources.get("https://api.dexscreener.com/latest/dex/pairs/solana/" + ",".join(chunk))
        got = {p["pairAddress"]: p for p in (d or {}).get("pairs") or []}
        if d is None:
            continue   # API failure is not evidence the pair vanished
        for pa in chunk:
            p = got.get(pa)
            db.conn().execute(
                "INSERT OR IGNORE INTO liquidity_obs VALUES (?,?,?,?,?,?)",
                (by_pair[pa]["id"], now,
                 sources.f(p.get("priceUsd")) if p else None,
                 (p.get("liquidity") or {}).get("usd") if p else None,
                 (p.get("marketCap") or p.get("fdv")) if p else None,
                 int(p is not None)))


# ------------------------------------------------------------------ bars
def fetch_bars(c: dict, cache: bool = True) -> list[tuple]:
    """1-min bars from T_D - 2h to T_D + 25h, cached in the ohlcv table.
    cache=False (interim results) neither reads nor writes the cache, so a
    partial window can never be mistaken for the complete one later."""
    cid = c["id"]
    rows = db.conn().execute("SELECT ts,o,h,l,c,v FROM ohlcv WHERE candidate_id=? ORDER BY ts",
                             (cid,)).fetchall() if cache else []
    if rows:
        return [tuple(r) for r in rows]
    td = int(db.parse_iso(c["t_decision"]).timestamp())
    start = td - 120 * 60
    before = td + (max(P["horizons_minutes"]) + 60) * 60
    bars: dict[int, list] = {}
    for _ in range(4):
        chunk = sources.ohlcv_minutes(c["pair_address"], before)
        if not chunk:
            break
        for b in chunk:
            bars[int(b[0])] = b
        earliest = int(chunk[0][0])
        if earliest <= start or len(chunk) < 900:
            break
        before = earliest
    out = [tuple([int(b[0])] + [float(x) for x in b[1:6]])
           for ts, b in sorted(bars.items()) if start <= ts < td + 1500 * 60]
    if cache:
        db.conn().executemany("INSERT OR IGNORE INTO ohlcv VALUES (?,?,?,?,?,?,?)",
                              [(cid,) + b for b in out])
    return out


def _last_close_at(bars, ts):
    last = None
    for b in bars:
        if b[0] + 60 <= ts:
            last = b[4]
        else:
            break
    return last


# ------------------------------------------------------------------ costs
def _fee(dex_id: str | None) -> float:
    return P["dex_fee"].get((dex_id or "").lower(), P["dex_fee_default"])


def _round_trip(entry_ref, exit_ref, liq_in, liq_out, fee) -> float:
    """Net return of a $position round trip under a CPMM impact model."""
    S = P["position_usd"]
    if not entry_ref or not liq_in or liq_in <= 0:
        return -1.0
    impact_in = S / (liq_in / 2)
    p_entry = entry_ref * (1 + fee + impact_in + P["slippage_buffer"])
    tokens = (S - P["tx_cost_usd"]) / p_entry
    if not exit_ref or not liq_out or liq_out < P["exit_liquidity_min_usd"]:
        return -1.0
    V = tokens * exit_ref
    impact_out = V / (liq_out / 2 + V)
    proceeds = V * (1 - fee - impact_out - P["slippage_buffer"]) - P["tx_cost_usd"]
    return max(proceeds, 0.0) / S - 1


# ------------------------------------------------------------------ barriers
def _first_touch(bars, t_fill, level, side, window_end):
    """Minutes from t_fill until price first trades through level.
    side='up' checks highs, 'down' checks lows. On the fill bar only the
    adverse (down) side is knowable, so 'up' ignores the fill bar."""
    for b in bars:
        ts, _, h, l, _, _ = b
        if ts + 60 <= t_fill or ts >= window_end:
            continue
        fill_bar = ts <= t_fill < ts + 60
        if side == "up" and not fill_bar and h >= level:
            return (ts - t_fill) / 60
        if side == "down" and l <= level:
            return max(0.0, (ts - t_fill) / 60)
    return None


def _race(up_t, dn_t):
    """Resolve an up-vs-down barrier race. Same bar -> loss."""
    if up_t is None and dn_t is None:
        return "neither"
    if dn_t is None:
        return "up"
    if up_t is None:
        return "down"
    return "up" if up_t < dn_t else "down"


def bracket(bars, t_fill, entry_ref, target, stop, max_minutes):
    """The v3 trade: take profit at entry*(1+target), stop at entry*(1+stop),
    else exit at the last close before t_fill + max_minutes.

    Returns (result, exit_ts, exit_price). Strictly sequential on 1-min bars.
    The stop is checked first, so a bar containing both levels is a loss. On
    the fill bar only the adverse side is knowable, so the target cannot fill
    there. The target is a resting limit and fills only if price traded
    THROUGH it (high > level), at the level. The stop is a market order once
    touched, filled at the worse of the level, the bar's open (a gap through)
    and its close - never better than the level."""
    up = entry_ref * (1 + target)
    dn = entry_ref * (1 + stop)
    end = t_fill + max_minutes * 60
    for ts, o, h, l, c, _ in bars:
        if ts + 60 <= t_fill or ts >= end:
            continue
        if l <= dn:
            return "stop", ts + 60, min(dn, o, c)
        if not (ts <= t_fill < ts + 60) and h > up:
            return "target", ts + 60, up
    return "time", end, _last_close_at(bars, end)


def _sigma_hourly(bars, t_fill):
    """Pre-entry realized vol: 1-min log returns on a forward-filled grid over
    the PREREG window ending at T_D, scaled to 1 hour."""
    n = P["pre_entry_sigma_minutes"]
    closes, last, j = [], None, 0
    pre = [b for b in bars if b[0] + 60 <= t_fill]
    start = t_fill - n * 60
    prior = [b for b in pre if b[0] + 60 <= start]
    last = prior[-1][4] if prior else None
    for m in range(n):
        edge = start + (m + 1) * 60
        while j < len(pre) and pre[j][0] + 60 <= edge:
            last = pre[j][4]
            j += 1
        if last:
            closes.append(last)
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    if len(rets) < n // 2:
        return None
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / max(1, len(rets) - 1)
    return math.sqrt(var) * math.sqrt(60)


# ------------------------------------------------------------------ evaluate
def evaluate(c: dict, max_minutes: int | None = None) -> dict:
    """Full evaluation, or with max_minutes a partial one (only horizons that
    have fully elapsed; nothing cached)."""
    cid = c["id"]
    e = db.conn().execute("SELECT * FROM entries WHERE candidate_id=?", (cid,)).fetchone()
    obs = db.conn().execute("SELECT t, price_usd, liquidity_usd, present FROM liquidity_obs "
                            "WHERE candidate_id=? ORDER BY t", (cid,)).fetchall()
    bars = fetch_bars(c, cache=max_minutes is None)
    t_fill = int(db.parse_iso(c["t_decision"]).timestamp())
    horizons = [h for h in P["horizons_minutes"] if max_minutes is None or h <= max_minutes]
    fee = _fee(c.get("dex_id"))
    out = {"candidate_id": cid, "bars": len(bars), "fee": fee,
           "position_usd": P["position_usd"], "notes": []}

    unfillable = not e or not e["ok"] or not e["liquidity_usd"]
    if unfillable and P.get("unfillable_is_no_trade") and c.get("prereg_version") == PREREG_VERSION:
        # Nothing to buy into: no position is opened, so nothing is won or lost.
        out.update(entry_ok=False, unfillable=True, returns={str(h): 0.0 for h in horizons},
                   bracket={"result": "unfilled", "net": 0.0})
        out["notes"].append("no pair or no liquidity at T_D: unfillable, no trade")
        return out
    if not e or not e["ok"]:
        out.update(entry_ok=False, returns={str(h): -1.0 for h in horizons})
        out["notes"].append("no executable quote at T_D: counted as total loss")
        return out

    next_open = next((b[1] for b in bars if b[0] >= t_fill and b[0] < t_fill + 300), None)
    entry_ref = max(x for x in (e["price_usd"], next_open) if x)
    out.update(entry_ok=True, entry_quote=e["price_usd"], next_bar_open=next_open,
               entry_ref=entry_ref, entry_liquidity=e["liquidity_usd"],
               entry_mcap=e["mcap_usd"])

    def liq_at(ts_iso):
        """Snapshot at or before ts. If it is stale (> 45 min old, e.g. the
        PC was off), take the WORSE of it and the first snapshot after ts,
        because a rug during the gap must not be hidden by an old reading."""
        before = after = None
        for o in obs:
            if o["t"] <= ts_iso:
                before = o
            elif after is None:
                after = o
        if before is None:
            return after
        gap = (db.parse_iso(ts_iso) - db.parse_iso(before["t"])).total_seconds()
        if gap <= 45 * 60 or after is None:
            return before
        out["notes"].append(f"stale exit liquidity near {ts_iso[:16]}: used worse of bracketing snapshots")
        if not after["present"]:
            return after
        if not before["present"]:
            return before
        return min(before, after, key=lambda o: o["liquidity_usd"] or 0)

    returns, exits = {}, {}
    for h in horizons:
        t_exit = t_fill + h * 60
        px = _last_close_at(bars, t_exit)
        lo = liq_at(db.iso(db.parse_iso(c["t_decision"]) + timedelta(minutes=h)))
        present = bool(lo and lo["present"])
        liq_out = lo["liquidity_usd"] if present else None
        if px is None and present:
            px = lo["price_usd"]
        returns[str(h)] = round(_round_trip(entry_ref, px, e["liquidity_usd"],
                                            liq_out, fee), 4)
        exits[str(h)] = {"exit_ref": px, "exit_liquidity": liq_out, "pair_present": present,
                         "gross_return": round(px / entry_ref - 1, 4) if px else None}
    out["returns"], out["exits"] = returns, exits

    bmax = P.get("bracket_max_minutes")
    if bmax and (max_minutes is None or max_minutes >= bmax):
        res, ts, px = bracket(bars, t_fill, entry_ref, P["bracket_target"],
                              P["bracket_stop"], bmax)
        lo = liq_at(db.iso(db.parse_iso(c["t_decision"]) + timedelta(seconds=ts - t_fill)))
        liq_out = lo["liquidity_usd"] if lo and lo["present"] else None
        if px is None and liq_out:
            px = lo["price_usd"]
        out["bracket"] = {"result": res, "exit_min": round((ts - t_fill) / 60, 1),
                          "exit_ref": px, "exit_liquidity": liq_out,
                          "net": round(_round_trip(entry_ref, px, e["liquidity_usd"],
                                                   liq_out, fee), 4)}

    liqs = [o["liquidity_usd"] for o in obs if o["present"] and o["liquidity_usd"]]
    out["min_liquidity_24h"] = min(liqs) if liqs else None
    out["liquidity_collapse"] = bool(
        any(not o["present"] for o in obs)
        or (liqs and e["liquidity_usd"] and min(liqs) < 0.2 * e["liquidity_usd"]))

    win_end = t_fill + P["barrier_window_minutes"] * 60
    post = [b for b in bars if b[0] + 60 > t_fill and b[0] < win_end]
    if post:
        after_fill = [b for b in post if b[0] >= t_fill]   # fill-bar high may predate entry
        if after_fill:
            out["mfe_6h"] = round(max(b[2] for b in after_fill) / entry_ref - 1, 4)
        out["mae_6h"] = round(min(b[3] for b in post) / entry_ref - 1, 4)
    sig = _sigma_hourly(bars, t_fill)
    out["sigma_hourly_pre"] = round(sig, 5) if sig else None
    if sig:
        out["sigma_barriers"] = {}
        for k in P["sigma_barriers_k"]:
            up = entry_ref * math.exp(k * sig)
            dn = entry_ref * math.exp(-k * sig)
            ut = _first_touch(bars, t_fill, up, "up", win_end)
            dt = _first_touch(bars, t_fill, dn, "down", win_end)
            out["sigma_barriers"][str(k)] = {"up_min": ut, "down_min": dt,
                                             "result": _race(ut, dt)}
    else:
        out["notes"].append("insufficient pre-entry bars for sigma")
    out["pct_levels_first_touch_min"] = {
        f"{t:+.0%}": _first_touch(bars, t_fill, entry_ref * (1 + t),
                                  "up" if t > 0 else "down", t_fill + 1440 * 60)
        for t in P["pct_targets"] + P["pct_stops"]}
    if len(bars) < 30:
        out["notes"].append("sparse OHLCV: few trades after entry or data gap")
    return out


def store(cid: int, outcome: dict):
    db.conn().execute("INSERT OR REPLACE INTO outcomes VALUES (?,?,?)",
                      (cid, db.iso(db.now_utc()), json.dumps(outcome)))
