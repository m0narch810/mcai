"""AI Crypto Narrative Researcher: Phase 1 CLI.

  python run.py run          start the experiment loop (leave it running)
  python run.py status       counts, budget, recent events
  python run.py analyze      experiment readout (also written to data/analysis.md)
  python run.py show ID      everything recorded for one candidate
  python run.py verify       check the hash-chained ledger and freeze triggers
  python run.py selftest     end-to-end smoke test on a live token (not stored)
  python run.py prereg       print the pre-registered parameters
  python run.py discord-test send a test message to the Discord webhook
"""
import json
import sys

# Windows consoles default to cp1252; token names and model text often aren't.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

from nr import db
from nr.config import PREREG, PREREG_VERSION, RUNTIME


def cmd_status():
    from nr.research import spent
    db.init()
    c = db.conn()
    q = lambda s, *a: c.execute(s, a).fetchone()[0]
    V = PREREG_VERSION
    cur = "FROM candidates c WHERE c.prereg_version=?"
    print(f"prereg {V}  (counts below are this version only; older versions are archived)")
    print(f"candidates      {q('SELECT COUNT(*) ' + cur, V)}")
    for r in c.execute("SELECT research_status, COUNT(*) " + cur + " GROUP BY 1", (V,)):
        print(f"  {r[0]:<14}{r[1]}")
    join = " JOIN candidates c ON c.id=t.candidate_id AND c.prereg_version=?"
    print(f"packets         {q('SELECT COUNT(*) FROM packets t' + join, V)}")
    for r in c.execute("SELECT arm, SUM(ok), SUM(late), COUNT(*) FROM reports t" + join +
                       " GROUP BY arm", (V,)):
        print(f"reports {r[0]:<3}     ok={r[1]} late={r[2]} total={r[3]}")
    print(f"entries         {q('SELECT COUNT(*) FROM entries t' + join + ' WHERE t.ok=1', V)} ok, "
          f"{q('SELECT COUNT(*) FROM entries t' + join + ' WHERE t.ok=0', V)} failed/missed")
    print(f"outcomes        {q('SELECT COUNT(*) FROM outcomes t' + join, V)}")
    print(f"postmortems     {q('SELECT COUNT(*) FROM postmortems t' + join + ' WHERE t.ok=1', V)}")
    print(f"coverage        {db.coverage(24):.0%} of last 24h observed, "
          f"{db.coverage(168):.0%} of last 7d")
    print(f"budget          24h ${spent(24):.2f}/{RUNTIME['daily_budget_usd']}  "
          f"5h ${spent(5):.2f}/{RUNTIME['window5h_budget_usd']}")
    from nr.research import session_share
    print(f"session (5h)    {session_share():.0%} used since bot's first run in window "
          f"(cap {RUNTIME['session_share_cap']:.0%})")
    print("\nrecent events:")
    for r in list(c.execute("SELECT t, level, msg FROM events ORDER BY rowid DESC LIMIT 15"))[::-1]:
        print(f"  {r[0][:19]} {r[1]:<5} {r[2][:150]}")


def cmd_show(cid):
    db.init()
    c = db.conn()
    cand = c.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
    if not cand:
        print("no such candidate"); return
    print(json.dumps(dict(cand), indent=1))
    for table, key in (("packets", "packet_json"), ("entries", None), ("outcomes", "outcome_json"),
                       ("postmortems", "postmortem_json")):
        r = c.execute(f"SELECT * FROM {table} WHERE candidate_id=?", (cid,)).fetchone()
        if r:
            print(f"\n== {table}")
            d = dict(r)
            if key and d.get(key):
                d[key] = json.loads(d[key])
            print(json.dumps(d, indent=1)[:6000])
    for r in c.execute("SELECT * FROM reports WHERE candidate_id=? ORDER BY id", (cid,)):
        d = dict(r)
        if d["report_json"]:
            d["report_json"] = json.loads(d["report_json"])
        print(f"\n== report arm={d['arm']}")
        print(json.dumps(d, indent=1))


def cmd_verify():
    db.init()
    ok, n, msg = db.verify_ledger()
    print(f"ledger: {msg} ({n} records)")
    c = db.conn()
    trig = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='trigger'")]
    print(f"freeze triggers: {len(trig)} ({', '.join(sorted(trig))})")
    try:
        c.execute("UPDATE reports SET error='tamper' WHERE id=(SELECT MIN(id) FROM reports)")
        c.execute("DELETE FROM candidates WHERE id=(SELECT MIN(id) FROM candidates)")
        print("WARNING: tamper attempt was not blocked" if
              c.execute("SELECT COUNT(*) FROM reports").fetchone()[0] else "no rows to test")
    except Exception as e:
        print(f"tamper attempt blocked: {e}")


def cmd_selftest():
    """Build a packet for a live trending token and run the P and C arms with
    a short timeout. Nothing is written to the experiment tables."""
    from datetime import timedelta
    from nr import packet, research, schemas, sources
    db.init()
    mints = sorted(sources.discover_tokens())
    pairs = sources.dex_pairs(mints[:60])
    best = None
    for m, ps in pairs.items():
        p = sources.best_pair(ps, PREREG["quote_tokens"])
        if p and 5e4 < (p.get("marketCap") or 0) < 3e7 and (p["liquidity"]["usd"] or 0) > 1e4:
            best = p
            break
    if not best:
        print("no suitable live token found"); return
    now = db.now_utc()
    fake = {"id": 0, "token": best["baseToken"]["address"], "symbol": best["baseToken"]["symbol"],
            "name": best["baseToken"]["name"], "pair_address": best["pairAddress"],
            "dex_id": best.get("dexId"), "t1_detected": db.iso(now),
            "t_decision": db.iso(now + timedelta(minutes=25)),
            "trigger_json": json.dumps({"selftest": True}), "prereg_version": "selftest"}
    print(f"token {fake['symbol']} {fake['token']}")
    pk = packet.build(fake)
    print(f"packet built: {len(json.dumps(pk))} bytes; limitations: {pk['data_limitations'][:2]}")
    pj = json.dumps(pk, indent=1)
    for arm, name, tools in (("P", "packet_only", ""), ("C", "research", research.WEB_TOOLS)):
        prompt = research._prompt(name, packet=pj, now_utc=db.iso(db.now_utc()),
                                  contract=fake["token"], symbol=fake["symbol"] or "")
        res = research.run_claude(prompt, schemas.REPORT, tools, 900,
                                  RUNTIME["per_run_budget_usd"], f"selftest_{arm}")
        print(f"\n== arm {arm}: ok={res['ok']} cost=${res.get('cost') or 0:.2f} "
              f"err={res.get('error')}")
        if res["ok"]:
            r = res["obj"]
            print(f"  p_runner={r.get('p_runner')} p_rug={r.get('p_rug')} "
                  f"continuation={r['continuation_view']} conf={r['research_confidence']} "
                  f"narrative={r['narrative']['potential']} conn={r['token_connection']['assessment']} "
                  f"feas={r['market_feasibility']['assessment']} auth={r['authenticity']['assessment']}")
            print(f"  thesis: {r['thesis'][:400]}")
            print(f"  coverage: {r['source_coverage']}")
            print(f"  sources: {len(r['sources'])}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "run":
        from nr.scheduler import run_forever
        run_forever()
    elif cmd == "status":
        cmd_status()
    elif cmd == "analyze":
        from nr.analysis import report
        db.init()
        print(report(sys.argv[2] if len(sys.argv) > 2 else None))
    elif cmd == "show":
        cmd_show(int(sys.argv[2]))
    elif cmd == "verify":
        cmd_verify()
    elif cmd == "selftest":
        cmd_selftest()
    elif cmd == "discord-test":
        from nr import notify
        print(notify.send_sync("✅ Narrative Researcher connected",
                               "You'll get every Claude research verdict, skeptic "
                               "flags, 24h results, errors, and a daily digest at 13:00 UTC."))
    elif cmd == "prereg":
        print(f"version {PREREG_VERSION}")
        print(json.dumps(PREREG, indent=1))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
