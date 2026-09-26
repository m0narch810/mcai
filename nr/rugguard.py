"""v6 rug guard: never trade a pool whose liquidity the deployer can pull.

An LP pull is the mechanism behind almost every -100% outcome: the pool is
emptied in one transaction, nothing can be sold at any price, and the chart
freezes (often above the stop, so the stop never fires). In v4/v5.1 coins
with removable LP rugged 72-79% of the time vs 11-21% for the rest, with the
same direction in both v5.1 halves and in v4.

Safe = still on the pump.fun bonding curve (there is no LP to pull), or the
top market's LP is locked (RugCheck) or burned (GMGN) above the thresholds.
If neither source can verify it, the coin is treated as removable."""
from . import db
from .config import PREREG

G = PREREG["rug_guard"]


def assess(trigger: dict, structure: dict, gmgn: dict) -> dict:
    if trigger.get("bonding_curve"):
        return {"lp_removable": False, "reason": "pump.fun bonding curve: no LP to pull"}
    locked = (structure.get("lp_locked_pct_top_markets") or [None])[0]
    burn = ((gmgn or {}).get("security") or {}).get("lp_burn_ratio")
    if locked is not None and locked >= G["lp_locked_min_pct"]:
        return {"lp_removable": False, "reason": f"LP {locked:.0f}% locked"}
    if burn is not None and burn >= G["lp_burn_min"]:
        return {"lp_removable": False, "reason": f"LP {burn:.0%} burned"}
    if locked is None and burn is None:
        return {"lp_removable": True, "reason": "LP lock/burn could not be verified"}
    return {"lp_removable": True,
            "reason": f"LP removable (locked {locked if locked is not None else '?'}%, "
                      f"burned {f'{burn:.0%}' if burn is not None else '?'})"}


def exclude_if_removable(cid: int, packet: dict) -> bool:
    """Record the exclusion (once) and return True if the coin must not be traded."""
    g = packet.get("rug_guard") or {}
    if not g.get("lp_removable"):
        return False
    t = db.iso(db.now_utc())
    db.conn().execute("INSERT OR IGNORE INTO exclusions VALUES (?,?,?)", (cid, t, g["reason"]))
    db.ledger("exclusion", {"candidate_id": cid, "t": t, "reason": g["reason"]})
    return True


def excluded(cid: int) -> bool:
    return db.conn().execute("SELECT 1 FROM exclusions WHERE candidate_id=?",
                             (cid,)).fetchone() is not None
