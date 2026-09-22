"""Experiment readout. Arms are compared on the SAME candidates at the SAME
entry time. Every table reports n. The null is sanity-checked first."""
import json
import math
import random
import statistics as st
from collections import Counter, defaultdict
from datetime import timedelta

from . import baselines, db
from .config import DATA_DIR, PREREG, PREREG_VERSION

MIN_N = 20
PH = str(PREREG["primary_horizon_minutes"])


def _load(version: str):
    c = db.conn()
    cands = {r["id"]: dict(r) for r in c.execute(
        "SELECT * FROM candidates WHERE prereg_version=?", (version,))}
    for r in c.execute("SELECT candidate_id, packet_json FROM packets"):
        if r[0] in cands:
            cands[r[0]]["packet"] = json.loads(r[1])
    for r in c.execute("SELECT candidate_id, outcome_json FROM outcomes"):
        if r[0] in cands:
            cands[r[0]]["outcome"] = json.loads(r[1])
    for r in c.execute("SELECT * FROM reports"):
        if r["candidate_id"] in cands:
            cands[r["candidate_id"]].setdefault("reports", {})[r["arm"]] = dict(r)
    for r in c.execute("SELECT candidate_id, postmortem_json FROM postmortems WHERE ok=1"):
        if r[0] in cands:
            cands[r[0]]["postmortem"] = json.loads(r[1])
    return cands


def auc(scores, labels):
    """Probability a random positive outranks a random negative (ties = 0.5)."""
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def boot_ci(scores, labels, n=1000, seed=7):
    rng = random.Random(seed)
    idx = list(range(len(scores)))
    vals = []
    for _ in range(n):
        s = [rng.choice(idx) for _ in idx]
        a = auc([scores[i] for i in s], [labels[i] for i in s])
        if a is not None:
            vals.append(a)
    if len(vals) < 50:
        return None
    vals.sort()
    return vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals))]


def spearman(x, y):
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r
    if len(x) < 3:
        return None
    rx, ry = ranks(x), ranks(y)
    mx, my = st.mean(rx), st.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else None


def _fmt(x, p=3):
    return "n/a" if x is None else f"{x:.{p}f}"


def _mcap_bucket(c):
    m = (c.get("outcome") or {}).get("entry_mcap") or \
        json.loads(c["trigger_json"]).get("mcap_usd") or 0
    return "<$300k" if m < 3e5 else "$300k-3M" if m < 3e6 else ">$3M"


def _runner(o):
    """+100% touched before -50% within 24h, sequentially."""
    lv = o.get("pct_levels_first_touch_min") or {}
    up, dn = lv.get("+100%"), lv.get("-50%")
    return up is not None and (dn is None or up < dn)


def _hour_coverage_line() -> str:
    """Which UTC hours the loop has actually observed over the last week."""
    rows = db.conn().execute(
        "SELECT substr(slot,12,2) h, COUNT(*) FROM heartbeat "
        "WHERE slot >= ? GROUP BY 1", (db.iso(db.now_utc() - timedelta(days=7)),)).fetchall()
    if not rows:
        return "Hours observed (7d): no heartbeat data yet"
    seen = {int(h): n for h, n in rows}
    bar = "".join("#" if seen.get(h, 0) >= 7 * 12 * 0.5 else
                  "-" if seen.get(h) else "." for h in range(24))
    return f"Hours observed (7d, UTC 00->23): {bar}   (# mostly up, - partial, . never)"


def report(version: str | None = None) -> str:
    version = version or PREREG_VERSION
    cands = _load(version)
    L = [f"# Narrative research readout: prereg {version}", ""]
    status = Counter(c["research_status"] for c in cands.values())
    done = [c for c in cands.values() if "outcome" in c]
    L += [f"Candidates: {len(cands)}  ({dict(status)})",
          f"With evaluated outcomes: {len(done)}",
          # The machine is off during school hours, so the sample is drawn
          # from part of the day, not all of it. Any hour-of-day effect in
          # Solana activity therefore lands in the sample as selection, not
          # as signal - state the duty cycle rather than implying 24h cover.
          f"Observation coverage: {db.coverage(24):.0%} of the last 24h, "
          f"{db.coverage(168):.0%} of the last 7d (loop uptime)",
          _hour_coverage_line(), ""]
    if not done:
        L.append("No evaluated outcomes yet. Outcomes arrive about 25h after detection.")
        return "\n".join(L)

    # ---------------- null sanity first
    L.append("## 1. Null sanity check (read this first)")
    k1 = [c["outcome"]["sigma_barriers"]["1.0"]["result"] for c in done
          if (c["outcome"].get("sigma_barriers") or {}).get("1.0")]
    decided = [r for r in k1 if r != "neither"]
    if decided:
        up = sum(r == "up" for r in decided) / len(decided)
        flag = ("n too small to judge" if len(decided) < 30 else
                "OK" if 0.35 <= up <= 0.65 else "INVESTIGATE: far from 0.5")
        L.append(f"Symmetric +/-1 sigma barrier, all candidates: up-first = {up:.3f} "
                 f"(n={len(decided)}, neither={k1.count('neither')}) -> {flag}")
        L.append("  Expect somewhat below 0.5 from memecoin negative drift, plus the "
                 "same-bar-counts-as-loss rule. Far below 0.35 or above 0.65 suggests a leak.")
    # The randomized control is `not_sampled` ONLY. `no_budget` candidates
    # passed the random draw and were then dropped because the bot had run
    # out of budget, which correlates with time of day and with how busy the
    # market is - not a random exclusion, so pooling it into the control
    # contaminates the comparison. It is reported separately below.
    ctrl = [c for c in done if c["research_status"] == "not_sampled"]
    rsch = [c for c in done if c["research_status"] == "selected"]
    nobud = [c for c in done if c["research_status"] == "no_budget"]
    if nobud:
        L.append(f"  Excluded from the control pool: {len(nobud)} `no_budget` "
                 f"candidates (non-random exclusion; see section 2b).")
    for h in map(str, PREREG["horizons_minutes"]):
        a = [c["outcome"]["returns"][h] for c in ctrl]
        b = [c["outcome"]["returns"][h] for c in rsch]
        if a and b:
            L.append(f"  Random-allocation check {h}m: researched median net "
                     f"{st.median(b):+.3f} (n={len(b)}) vs control {st.median(a):+.3f} "
                     f"(n={len(a)}). These should NOT differ systematically; research "
                     f"does not change the price.")
    L.append("")

    # ---------------- base rates
    L.append("## 2. Base rates (all candidates, net of costs)")
    for h in map(str, PREREG["horizons_minutes"]):
        r = [c["outcome"]["returns"][h] for c in done]
        L.append(f"  {h}m: median {st.median(r):+.3f}  mean {st.mean(r):+.3f}  "
                 f"win% {sum(x > 0 for x in r) / len(r):.2f}  total-loss% "
                 f"{sum(x <= -0.99 for x in r) / len(r):.2f}  (n={len(r)})")
    if nobud:
        L.append("")
        L.append("## 2b. Non-random exclusions (`no_budget`)")
        L.append(f"  {len(nobud)} candidates passed the random draw but found the "
                 f"budget empty. They are neither treatment nor control. If this "
                 f"group's returns differ from `not_sampled`, the budget cap is "
                 f"selecting on market conditions and the daily budget or the "
                 f"sampling probability needs to change.")
        for h in map(str, PREREG["horizons_minutes"]):
            nb = [c["outcome"]["returns"][h] for c in nobud]
            ns = [c["outcome"]["returns"][h] for c in ctrl]
            if nb and ns:
                L.append(f"  {h}m: no_budget median {st.median(nb):+.3f} (n={len(nb)}) "
                         f"vs not_sampled {st.median(ns):+.3f} (n={len(ns)})")
    coll = sum(bool(c["outcome"].get("liquidity_collapse")) for c in done)
    L.append(f"  Liquidity collapse / pair vanished within 24h: {coll}/{len(done)}")
    L.append("")

    # ---------------- arm comparison on common subset
    L.append("## 3. Arm comparison: same candidates, same entry (primary)")
    rows = []
    for c in done:
        p, reps = c.get("packet"), c.get("reports") or {}
        cc, pp = reps.get("C"), reps.get("P")
        if not p or not cc or not pp or not cc["ok"] or not pp["ok"] or cc["late"] or pp["late"]:
            continue
        rows.append({
            "A": baselines.arm_a(p), "B": baselines.arm_b(p),
            "P": baselines.arm_claude(json.loads(pp["report_json"])),
            "C": baselines.arm_claude(json.loads(cc["report_json"])),
            "yP": c["outcome"]["returns"][PH] > 0,
            "y24": c["outcome"]["returns"]["1440"] > 0,
            "y6": c["outcome"]["returns"]["360"] > 0,
            "r6": c["outcome"]["returns"]["360"], "r24": c["outcome"]["returns"]["1440"],
            "runner": _runner(c["outcome"]),
        })
    rows = [r for r in rows if r["A"] is not None and r["B"] is not None]
    L.append(f"Common subset (all four arms valid, none late): n={len(rows)}")
    if len(rows) < MIN_N:
        L.append(f"  n < {MIN_N}: AUCs below are unstable, so treat them as a pipeline check only.")
    # A score with no spread cannot rank, so its AUC is 0.5 by construction
    # rather than by measurement. Say which it is before anyone reads the
    # table: an arm that always returns the same label is not uninformative,
    # it is unmeasurable, and the two look identical in the AUC column.
    L.append("  Score spread (distinct values / n) - an arm with 1 distinct "
             "value has AUC 0.5 by construction:")
    for arm in ("A", "B", "P", "C"):
        vals = Counter(r[arm] for r in rows)
        flag = ("DEGENERATE: cannot rank" if len(vals) < 2 else
                "thin: ranking is coarse" if len(vals) == 2 else "ok")
        L.append(f"    {arm:>3}: {len(vals)} distinct of {len(rows)} -> {flag}  {dict(vals)}")
    L.append(f"  arm | PRIMARY AUC(net{PH}m>0) [95% CI] | AUC(net24h>0) | AUC(runner) | "
             f"Spearman(score, net{PH}m)")
    for arm in ("A", "B", "P", "C"):
        s = [r[arm] for r in rows]
        aP = auc(s, [r["yP"] for r in rows])
        ci = boot_ci(s, [r["yP"] for r in rows]) if rows else None
        L.append(f"  {arm:>3} | {_fmt(aP)} [{_fmt(ci[0]) if ci else 'n/a'}, "
                 f"{_fmt(ci[1]) if ci else 'n/a'}] | {_fmt(auc(s, [r['y24'] for r in rows]))} | "
                 f"{_fmt(auc(s, [r['runner'] for r in rows]))} | "
                 f"{_fmt(spearman(s, [r['r6'] for r in rows]))}")
    L.append("  Legend: A=quant, B=quant+structured social (GMGN wallets/dev; no X posts), "
             "P=Claude packet-only, C=Claude full research. 0.5 = no information.")
    L.append("")

    # ---------------- C labels vs outcomes, split by regime
    L.append(f"## 4. Claude continuation_view vs net {PH}m outcome, split by market-cap bucket")
    by = defaultdict(list)
    for c in done:
        cc = (c.get("reports") or {}).get("C")
        if cc and cc["ok"] and not cc["late"]:
            v = json.loads(cc["report_json"])["continuation_view"]
            by[(_mcap_bucket(c), v)].append(c["outcome"]["returns"][PH])
    for key in sorted(by):
        r = by[key]
        L.append(f"  {key[0]:>9} {key[1]:>16}: n={len(r):3d} median{PH}m {st.median(r):+.3f} "
                 f"win% {sum(x > 0 for x in r) / len(r):.2f}")
    L.append("")

    # ---------------- by token age (no age cap in the universe; split instead)
    L.append(f"## 4b. Claude continuation_view vs net {PH}m outcome, split by pair age")
    by = defaultdict(list)
    for c in done:
        cc = (c.get("reports") or {}).get("C")
        if cc and cc["ok"] and not cc["late"]:
            age_h = json.loads(c["trigger_json"]).get("pair_age_min", 0) / 60
            bucket = "<6h" if age_h < 6 else "6h-3d" if age_h < 72 else ">3d"
            v = json.loads(cc["report_json"])["continuation_view"]
            by[(bucket, v)].append(c["outcome"]["returns"][PH])
    for key in sorted(by):
        r = by[key]
        L.append(f"  {key[0]:>6} {key[1]:>16}: n={len(r):3d} median{PH}m {st.median(r):+.3f} "
                 f"win% {sum(x > 0 for x in r) / len(r):.2f}")
    L.append("")

    # ---------------- outcome categories (master plan section 16)
    L.append("## 5. Outcome categories (runner = +100% before -50% within 24h)")
    cat = Counter()
    for c in done:
        cc = (c.get("reports") or {}).get("C")
        if not (cc and cc["ok"] and not cc["late"]):
            continue
        rep = json.loads(cc["report_json"])
        run = "runner" if _runner(c["outcome"]) else "failed"
        narr = "narrative" if rep["narrative"]["potential"] in ("moderate", "strong") else "no-narrative"
        conn = "strong-conn" if rep["token_connection"]["assessment"] in ("moderate", "strong") else "weak-conn"
        cat[f"{narr} -> {run}"] += 1
        cat[f"{conn} -> {run}"] += 1
    for k, v in sorted(cat.items()):
        L.append(f"  {k:<28} {v}")
    L.append("")

    # ---------------- self-consistency
    L.append("## 6. Self-consistency (C vs independent rerun C2)")
    pairs = []
    for c in done:
        reps = c.get("reports") or {}
        if all(reps.get(a) and reps[a]["ok"] for a in ("C", "C2")):
            pairs.append((baselines.arm_claude(json.loads(reps["C"]["report_json"])),
                          baselines.arm_claude(json.loads(reps["C2"]["report_json"]))))
    if pairs:
        exact = sum(a == b for a, b in pairs) / len(pairs)
        within1 = sum(abs(a - b) <= 0.25 + 1e-9 for a, b in pairs) / len(pairs)
        L.append(f"  n={len(pairs)}  exact agreement {exact:.2f}  within one step {within1:.2f}")
        L.append("  Low agreement means C's labels are mostly noise, whatever its AUC says.")
    else:
        L.append("  No rerun pairs yet.")
    L.append("")

    # ---------------- skeptic
    L.append("## 7. Skeptic")
    sk = [(baselines.arm_skeptic(json.loads(c["reports"]["S"]["report_json"])),
           c["outcome"]["returns"]["1440"] > 0) for c in done
          if (c.get("reports") or {}).get("S") and c["reports"]["S"]["ok"]
          and not c["reports"]["S"]["late"]]
    L.append(f"  n={len(sk)}  AUC(skeptic view, net24h>0) = "
             f"{_fmt(auc([s for s, _ in sk], [y for _, y in sk]))}")
    L.append("")

    # ---------------- coverage
    L.append("## 8. Source coverage (C arm): X/Twitter accessibility vs outcome")
    cov = defaultdict(list)
    for c in done:
        cc = (c.get("reports") or {}).get("C")
        if cc and cc["ok"]:
            x = json.loads(cc["report_json"])["source_coverage"]["x_twitter"]
            cov[x].append(c["outcome"]["returns"]["1440"] > 0)
    for k, v in cov.items():
        L.append(f"  {k:<24} n={len(v):3d} win% {sum(v) / len(v):.2f}")
    L.append("  If win% differs a lot by coverage, visibility itself is a hidden variable.")
    L.append("")

    # ---------------- ops
    late = sum(1 for c in cands.values() for r in (c.get("reports") or {}).values() if r["late"])
    fail = sum(1 for c in cands.values() for r in (c.get("reports") or {}).values() if not r["ok"])
    cost = db.conn().execute("SELECT COALESCE(SUM(cost_usd),0) FROM usage").fetchone()[0]
    L += ["## 9. Operations",
          f"  Late reports (excluded): {late}   Failed runs: {fail}   "
          f"Metered cost (plan-equivalent): ${cost:.2f}", ""]

    # ---------------- hypotheses
    hyps = [h for c in done for h in (c.get("postmortem") or {}).get("hypotheses_proposed", [])]
    L.append(f"## 10. Hypotheses proposed by post-mortems ({len(hyps)})")
    L.append("  These are PROPOSALS only. Each must be pre-registered and tested on "
             "candidates detected AFTER it was written.")
    for h in hyps[-15:]:
        L.append(f"  - {h['statement']}  [when: {h['conditions']}]")
    text = "\n".join(L)
    (DATA_DIR / "analysis.md").write_text(text, encoding="utf-8")
    return text
