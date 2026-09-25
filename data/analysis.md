# Narrative research readout: prereg e16de5aac8ee

Candidates: 329  ({'not_sampled': 129, 'selected': 134, 'no_budget': 66})
With evaluated outcomes: 128
Observation coverage: 100% of the last 24h, 43% of the last 7d (loop uptime)
Hours observed (7d, UTC 00->23): ------------------------   (# mostly up, - partial, . never)

## 1. Null sanity check (read this first)
Symmetric +/-1 sigma barrier, all candidates: up-first = 0.277 (n=47, neither=42) -> INVESTIGATE: far from 0.5
  Expect somewhat below 0.5 from memecoin negative drift, plus the same-bar-counts-as-loss rule. Far below 0.35 or above 0.65 suggests a leak.
  Excluded from the control pool: 21 `no_budget` candidates (non-random exclusion; see section 2b).
  Random-allocation check 60m: researched median net -0.059 (n=58) vs control -0.072 (n=49). These should NOT differ systematically; research does not change the price.
  Random-allocation check 360m: researched median net -0.557 (n=58) vs control -0.341 (n=49). These should NOT differ systematically; research does not change the price.
  Random-allocation check 1440m: researched median net -0.676 (n=58) vs control -0.471 (n=49). These should NOT differ systematically; research does not change the price.

## 1b. Trading test: buy at T_D, +100% target, -50% stop, else 6h (PRIMARY)
  All candidates: {'unfilled': 38, 'time': 45, 'stop': 35, 'target': 10}  (unfilled = no pair/liquidity at T_D, no trade)
  Null check, control pool: target-first 0.267 of decided (n=15) -> n too small to judge
    +100%/-50% is log-symmetric, so a driftless price gives 0.5. Memecoin decay and rugs should pull this well BELOW 0.5; above ~0.6 suggests a fill leak.
  Buy-everything baseline (control, filled): mean net -0.460  median -0.548  target-hit 0.12  (n=34)
  Arm C: n=39  distinct p_runner values 20  AUC(p_runner, target hit) 0.775 [0.529, 0.971]
    TAKEN (p_runner >= 35) n=  1  target-hit 1.00  mean net +0.814  median +0.814  P&L $+204
    skipped                n= 38  target-hit 0.13  mean net -0.546  median -0.680  P&L $-5,190
    calibration (p_runner bin -> observed target rate): 0-19: 0.11 (n=35), 20-39: 0.50 (n=4)
    AUC(p_rug, liquidity collapse within 24h) 0.663
  Arm P: n=39  distinct p_runner values 20  AUC(p_runner, target hit) 0.750 [0.392, 1.000]
    skipped                n= 39  target-hit 0.15  mean net -0.511  median -0.641  P&L $-4,986
    calibration (p_runner bin -> observed target rate): 0-19: 0.07 (n=30), 20-39: 0.44 (n=9)
    AUC(p_rug, liquidity collapse within 24h) 0.596

## 2. Base rates (all candidates, net of costs)
  60m: median -0.108  mean -0.297  win% 0.16  total-loss% 0.21  (n=128)
  360m: median -0.637  mean -0.527  win% 0.03  total-loss% 0.37  (n=128)
  1440m: median -0.711  mean -0.522  win% 0.02  total-loss% 0.37  (n=128)

## 2b. Non-random exclusions (`no_budget`)
  21 candidates passed the random draw but found the budget empty. They are neither treatment nor control. If this group's returns differ from `not_sampled`, the budget cap is selecting on market conditions and the daily budget or the sampling probability needs to change.
  60m: no_budget median -0.335 (n=21) vs not_sampled -0.072 (n=49)
  360m: no_budget median -0.992 (n=21) vs not_sampled -0.341 (n=49)
  1440m: no_budget median -0.992 (n=21) vs not_sampled -0.471 (n=49)
  Liquidity collapse / pair vanished within 24h: 34/128

## 3. Arm comparison: same candidates, same entry (primary)
Common subset (all four arms valid, none late): n=58
  Score spread (distinct values / n) - an arm with 1 distinct value has AUC 0.5 by construction:
      A: 58 distinct of 58 -> ok  {-0.07229598959677475: 1, 0.2974710359898337: 1, 0.4384633899573675: 1, 0.40326055704114583: 1, 0.567619328929334: 1, 0.7347819223195254: 1, -0.13455459774423217: 1, 0.4987283534292444: 1, 0.5444812923390041: 1, 0.38833607339442683: 1, 1.1870327043709037: 1, 0.3787146729641713: 1, 0.2682430842046827: 1, -0.1794667601606338: 1, 0.5467154138928472: 1, 0.45014023531880987: 1, 0.8063702293315197: 1, -0.1969103496760157: 1, 0.8203837597535419: 1, 0.30581061426710787: 1, 0.8880623476177254: 1, 0.4515998044208053: 1, 0.7413958766534771: 1, 0.4908209333496731: 1, 0.7658185671743649: 1, -0.19463011752308856: 1, 0.8066913597928217: 1, 0.5450564685667956: 1, 1.0784942906191317: 1, 0.6674443986496736: 1, 0.6130265306423568: 1, 0.3460658950511518: 1, -0.218909302193601: 1, 0.4362730456915704: 1, -0.14734944303843472: 1, -0.1841841774544165: 1, -0.2352412912099242: 1, -0.2835907402992655: 1, 0.8048880621068619: 1, 0.8204249096829601: 1, 0.16999096953397372: 1, 0.8490308092547842: 1, 0.4042582418269587: 1, 0.5232982460413098: 1, 0.025393686607677865: 1, 0.5948737940828293: 1, 0.8170653601112541: 1, 0.6746840258410235: 1, 0.732420050230579: 1, 0.8314124051908874: 1, 0.6706871650872119: 1, -0.09542681406783177: 1, 0.6963091597313041: 1, 0.16588585367802808: 1, -0.2727460392300202: 1, 0.7248071139145722: 1, 0.1932277498700867: 1, -0.12458615845614303: 1}
      B: 58 distinct of 58 -> ok  {-0.21471466146505402: 1, 0.04688366614306501: 1, -0.04960163835464962: 1, 0.06274138963168402: 1, 0.30421381328304303: 1, 0.2100617766448054: 1, -0.21393285442767165: 1, 0.06139751004795552: 1, 0.13335175728061316: 1, 0.22593020093149396: 1, 0.4104719077410074: 1, 0.05046844759319674: 1, -0.004767346786547555: 1, -0.24665560230253913: 1, 0.053835484724201355: 1, 0.25528043004183365: 1, 0.2125406702213154: 1, -0.26656628594911896: 1, 0.1948141020989932: 1, 0.014016418244665041: 1, 0.26022006269775155: 1, -0.05865565334515288: 1, 0.21280904943784965: 1, 0.1605464619522386: 1, 0.503090765068664: 1, -0.2661706143170999: 1, 0.18512345767418864: 1, 0.3610755837028635: 1, 0.5627471453095658: 1, 0.414018236258594: 1, 0.32528436168862435: 1, -0.09696705247442411: 1, -0.279710206652356: 1, 0.003701722305783667: 1, -0.21825396650366333: 1, -0.23444911148943198: 1, -0.35359989058940805: 1, -0.3058842590385217: 1, 0.22802034162454055: 1, 0.23165543207925632: 1, -0.010923774961124247: 1, 0.24071540462739213: 1, 0.16861940427742964: 1, 0.3471550199281024: 1, 0.017315245797458922: 1, 0.1574146748191924: 1, 0.2602660133889604: 1, 0.3474086795871784: 1, 0.17343224733751172: 1, 0.2370728692621104: 1, 0.38003987883990226: 1, -0.2012356292561381: 1, 0.17553235764342986: 1, -0.06883485093876375: 1, -0.3074952418372323: 1, 0.2980946823432105: 1, 0.04426943049059891: 1, -0.21424863478362705: 1}
      P: 20 distinct of 58 -> ok  {0.07: 12, 0.1: 5, 0.14: 4, 0.2: 3, 0.19: 1, 0.18: 4, 0.15: 1, 0.08: 3, 0.06: 5, 0.12: 1, 0.09: 5, 0.13: 1, 0.24: 2, 0.05: 3, 0.11: 2, 0.3: 1, 0.22: 2, 0.27: 1, 0.17: 1, 0.01: 1}
      C: 21 distinct of 58 -> ok  {0.05: 9, 0.12: 5, 0.09: 3, 0.08: 4, 0.15: 2, 0.06: 4, 0.11: 2, 0.18: 1, 0.07: 8, 0.22: 1, 0.1: 5, 0.04: 2, 0.13: 3, 0.16: 1, 0.36: 1, 0.17: 1, 0.24: 1, 0.2: 1, 0.21: 1, 0.14: 2, 0.01: 1}
  arm | PRIMARY AUC(net360m>0) [95% CI] | AUC(net24h>0) | AUC(runner) | Spearman(score, net360m)
    A | 0.384 [0.193, 0.596] | 0.509 | 0.687 | -0.226
    B | 0.411 [0.218, 0.614] | 0.526 | 0.703 | -0.169
    P | 0.830 [0.616, 0.991] | 0.956 | 0.586 | 0.026
    C | 0.710 [0.561, 0.851] | 0.772 | 0.641 | -0.074
  Legend: A=quant, B=quant+structured social (GMGN wallets/dev; no X posts), P=Claude packet-only, C=Claude full research. 0.5 = no information.

## 4. Claude continuation_view vs net 360m outcome, split by market-cap bucket
   $300k-3M             fade: n=  1 median360m -1.000 win% 0.00
   $300k-3M      strong_fade: n=  2 median360m -1.000 win% 0.00
     <$300k             fade: n= 21 median360m -0.819 win% 0.10
     <$300k          neutral: n=  2 median360m -0.384 win% 0.00
     <$300k      strong_fade: n= 32 median360m +0.000 win% 0.00

## 4b. Claude continuation_view vs net 360m outcome, split by pair age
     <6h             fade: n= 21 median360m -0.840 win% 0.10
     <6h      strong_fade: n= 34 median360m +0.000 win% 0.00
     >3d             fade: n=  1 median360m -0.316 win% 0.00
     >3d          neutral: n=  2 median360m -0.384 win% 0.00

## 5. Outcome categories (runner = +100% before -50% within 24h)
  narrative -> failed          21
  narrative -> runner          4
  no-narrative -> failed       28
  no-narrative -> runner       5
  strong-conn -> failed        10
  strong-conn -> runner        3
  weak-conn -> failed          39
  weak-conn -> runner          6

## 6. Self-consistency (C vs independent rerun C2)
  n=6  exact agreement 0.17  within one step 1.00
  Low agreement means C's labels are mostly noise, whatever its AUC says.

## 7. Skeptic
  n=0  AUC(skeptic view, net24h>0) = n/a

## 8. Source coverage (C arm): X/Twitter accessibility vs outcome
  searched_nothing_found   n= 19 win% 0.00
  inaccessible             n= 34 win% 0.03
  searched_found           n=  5 win% 0.00
  If win% differs a lot by coverage, visibility itself is a hidden variable.

## 9. Operations
  Late reports (excluded): 0   Failed runs: 0   Metered cost (plan-equivalent): $126.07

## 10. Hypotheses proposed by post-mortems (65)
  These are PROPOSALS only. Each must be pre-registered and tested on candidates detected AFTER it was written.
  - Holder concentration measured after excluding the AMM account understates control risk. When the excluded AMM share is ≥50% and LP providers are few or unlocked, top-10 holder % should not count as evidence of dispersed ownership.  [when: The RugCheck/GMGN top-10 figure excludes the AMM, the excluded AMM share is ≥50%, and the LP is unlocked or has few providers.]
  - On pump.fun bonding-curve tokens under 2 hours old, where GMGN bundler_wallets / GMGN holder_count >= 0.4 and creator_token_status is creator_close (or creator_hold_rate = 0), the share that touch -50% from entry within 6h is materially higher than the base rate for bonding-curve candidates in the same universe.  [when: Applies only to bonding-curve (unmigrated) tokens in the $10k-$300k mcap universe, with GMGN data available at T2. Compare against all curve candidates without both flags. Needs at least 30 flagged cases before drawing conclusions.]
  - Among curve tokens with curve progress >= 65% at T2 and a high bundler share (>= 0.4), touching +50% before -50% does NOT predict migration. Most will hit -50% within 3h without migrating.  [when: Bonding-curve tokens with virtual-SOL progress logged at T2. Measure the order of first touches and whether migrated_unix becomes non-zero within 6h.]
  - For curve tokens, the gap between the T2 quote and the entry reference (entry markup) is a negative predictor of 6h return, independent of p_runner.  [when: All candidates with entry_ok. Compute (entry_ref / T2 price - 1) and correlate it with the 360-min return.]
  - When a token's metadata (website or X handle) links it to a project described as a pump.fun token, but the mint has no pump.fun curve origin (GMGN migrated_unix=0 and empty launchpad, pool created directly on PumpSwap/Raydium), and 0% of LP is locked or burned, it will be unfillable or have lost at least 80% of its liquidity within 60 minutes of detection in at least 70% of cases.  [when: Solana tokens where the canonical same-name token can be identified at detection time, the candidate is not that canonical mint, the LP is supplied by a single wallet, and lp_burn_ratio=0.]
  - If at detection the h1 unique-seller to unique-buyer ratio is below 0.05 with more than 1,000 unique buyers, and the seller count barely changes across the m5/m15/m30 windows, the token's liquidity will be removed before a typical 20 to 30 minute decision delay at a higher rate than tokens with a ratio of 0.2 or more at similar market cap and age.  [when: Tokens under 2 hours old, $50k to $300k market cap, with GeckoTerminal unique buyer and seller data available.]
  - When 5 or more tokens with the same name or ticker were created within the previous 6 hours and most of them already show $0 liquidity, a new non-canonical token with that name will be unfillable at T_D, or lose at least 80% of its liquidity within 1 hour, at a rate above 70%.  [when: Same-name search at T2 returns 5 or more recent (<6h) pairs, and the candidate is not the canonical mint (canonical = highest liquidity and actual launchpad origin).]
  - For candidates with unlocked, single-provider LP where liquidity/mcap > 1, the share that is unfillable at T_D rises as the gap between detection and decision grows (e.g. 10 vs 25 minutes).  [when: A process check on the experiment pipeline itself, measured across all candidates that meet the LP condition.]
  - When a candidate belongs to a same-name cluster of 5 or more tokens launched within 90 minutes, and at least 40% of those siblings already show liquidity below $1k at T2, the candidate's liquidity falls below 10% of its entry liquidity within 6 hours in more than 50% of cases, and it reaches +100% before -50% in fewer than 15% of cases.  [when: Applies to young tokens (under 3 hours old) where the evidence packet's same_ticker_or_name_tokens list has 5 or more entries with pair_created_at within 90 minutes of each other. Sibling liquidity is measured at T2 only.]
  - Tokens whose h1 unique buyers exceed 10x h1 unique sellers, and whose m30 unique buyers are below 10% of h1 unique buyers, do not reach +100% from decision-time entry within 6 hours at a higher rate than the universe base rate.  [when: Requires GeckoTerminal unique buyer and seller counts at T2 for a pair younger than 2 hours. Does not apply to tokens still on a bonding curve, where the airdrop-style buyer counts may reflect curve mechanics.]
  - When RugCheck flags LP unlocked at 'danger' level and GMGN reports lp_burn_ratio of 1.0 or higher for the same token, the rate at which liquidity collapses to near $0 within 6 hours matches the rate for tokens where both sources agree LP is unlocked, and is higher than the rate for tokens where both agree it is burned or locked.  [when: Applies to any token with conflicting LP-lock readings across sources in the evidence packet, especially tokens with more than one pool (Meteora plus PumpSwap).]
  - In coordinated or same-name clusters, a candidate whose price rises more than 2x between T2 collection and decision-time entry has a higher 6-hour liquidity-collapse rate than cluster candidates that did not rise.  [when: Compare T2 price_usd with entry_ref for tokens the research labelled 'coordinated' or with ticker_hijack_risk 'high'.]
  - Solana tokens whose AMM pool was created by the deployer (not a bonding-curve migration, pool created within about 5 minutes of the mint) with LP locked or burned below 10% and liquidity/market cap at or above 1.0 will see liquidity fall by 90% or more within 6 hours of detection far more often than other tokens in the universe (e.g., at least 60% of cases).  [when: Applies to tokens in the $10k-$300k band on PumpSwap or Raydium where migrated_unix = 0 or the mint lacks a launchpad signature, RugCheck/GMGN report LP burn/lock below 10%, and pair age is under 2 hours at detection. Does not apply to pump.fun-migrated pools, where the protocol burns the LP.]
  - When unique_sellers / unique_buyers is below 0.05 and sells per unique seller exceed 30 in the first hour, the token will fail to touch +50% within 6 hours in most cases and will show max adverse excursion of -50% or worse more often than the base rate.  [when: Use GeckoTerminal unique buyer/seller counts at T2, for pairs younger than 3 hours with at least 1,000 total transactions in h1. Test separately for pools with locked or burned LP and pools without, to isolate the effect from LP control.]
  - Holder-concentration metrics that exclude AMM accounts should be adjusted to treat the pool as controlled by the deployer whenever LP is unlocked. After that adjustment, effective concentration above 60% predicts liquidity collapse better than the raw top-10 figure does.  [when: Tokens where the excluded pool account holds 50% or more of supply and LP lock/burn is below 10%. Compare predictive power (e.g., AUC for a 6h liquidity collapse) of the raw vs adjusted concentration on future candidates.]