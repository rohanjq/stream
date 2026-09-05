# Market Structure — Master Implementation Spec

Distilled from the Udemy "Market Structure" course (NCI trading system) at
`/Users/rohan.arora/udemy/downloader/courses/marketstructure`. Built by leaning on the
pre-distilled `INDICATOR_RULE_SPECIFICATION.md` (IRS), `COURSE_TECHNICAL_KNOWLEDGE.md`
(CTK), `TRANSCRIPT_COVERAGE.md`, and `MarketStructure_Course.pine`, then cross-checking
transcripts.

Target: programmatic detection + drawing on a live candlestick chart (TradingView
Lightweight Charts) fed OHLC bars `{time, open, high, low, close}` on 1m/5m/15m/30m.

> **Fidelity note.** Terms tagged **[COURSE]** are explicitly taught. **[JUDGMENT]** are
> taught visually without a full numeric definition. **[ENGINEERING]** are my deterministic
> resolutions of gaps — safe defaults, not course facts. This course has a *specific* method;
> it is NOT generic ICT. In particular it does NOT use N-bar fractal pivots, and it does NOT
> use the words BOS/CHoCH/MSS/order-block/FVG. Those mappings are approximate and flagged.

---

## (a) Concept Glossary

| Course term | Meaning | Nearest generic term |
|---|---|---|
| **Pulse wave (PW)** | Leg moving *with* the main trend | impulse |
| **Pullback wave (PB)** | Leg moving *against* the trend; must pass a price-action pattern to count | retracement |
| **Recent high / recent low** | The current structural extreme the next pulse must break | swing high/low being targeted |
| **Current key level** | The most recent *valid* pullback extreme whose following pulse validly broke the recent extreme. Only ONE per timeframe defines the trend. | last protected swing |
| **Old key level** | A former current key, archived. No longer defines trend; still acts as target / reaction / risk zone. | broken structure |
| **Internal structure** | Micro highs/lows *between* current key and recent extreme. Cannot redefine main structure until a valid break promotes it. | inducement / internal range |
| **Valid pullback** | A candidate pullback confirmed by a price-action pattern (§b2) | — |
| **Valid breakout** | A close-through of a reference by a qualifying Marubozu pattern (§b3) | BOS / break |
| **Continuation break** | Valid break of recent extreme in trend direction → new pulse | **≈ BOS** [approx] |
| **Trend change** | Valid break of the active *opposing* key → trend flips | **≈ CHoCH / MSS** [approx] |
| **Range** | Horizontal / indecisive movement with no valid breakout | consolidation |
| **Fake breakout** | Invalid excursion; expands the range boundary rather than moving structure | liquidity sweep |
| **Marubozu / Pinbar / Doji / Normal** | The four-candle vocabulary (§b1) | — |
| **PAC** | Price-Action Confirmation: repairs a 2-candle pattern with 1 failed candle | — |
| **POI** | Point of Interest (imbalance + 61.8–80% fib + first opposite candle). Imported from a *companion* smart-money course; **incomplete here** | order block + FVG + OTE |
| **Market cycle** | An LTF trend "ends" when it reaches an opposing HTF key/POI/extreme | premium/discount exhaustion |
| **TF/2** | Advanced: validate an unclear HTF pattern from LTF composition | MTF confirmation |

> **Caution on labels:** `MarketStructure_Course.pine` prints "CTS" for continuation and
> "BOS" for trend-change — this is idiosyncratic and *contradicts* the CTK, which explicitly
> says "no CTS rule exists" and maps continuation≈BOS, trend-change≈CHoCH. **Use the CTK
> mapping** (continuation=BOS, key-break=CHoCH); ignore the .pine's label strings.

---

## (b) Core Detection Algorithms (operate on closed-bar OHLC array)

### Preliminaries — derived candle fields (per closed bar `i`)

```
R  = high - low                       // total range
B  = |close - open|                   // body
BR = R>0 ? B/R : 0                    // body ratio
mid = (high + low)/2                  // MIDPOINT of TOTAL range
dir = close>open ? UP : close<open ? DOWN : FLAT
closeLoc = R>0 ? (close-low)/R : 0.5  // 0=at low, 1=at high
upperWick = high - max(open,close)
lowerWick = min(open,close) - low
eps = mintick * equalityToleranceTicks   // default 0 → strict
strictAbove(a,b) = a > b + eps ;  strictBelow(a,b) = a < b - eps
```

Process **only `isClosed` bars, exactly once**. Never form a final signal from the live bar.
Never use future/right-side bars to pick a pivot. (§d non-repaint rules.)

### b1. Candle classification [COURSE]

```
STANDARD_MARU  := R>0 and BR >= 0.70 and dir != FLAT
SPECIAL_MARU_UP   := R>0 and dir=UP   and BR>=0.50 and closeLoc>=0.90 and lowerWick>0
SPECIAL_MARU_DOWN := R>0 and dir=DOWN and BR>=0.50 and closeLoc<=0.10 and upperWick>0
PINBAR := R>0 and BR < 0.50
NORMAL := none of the above and not tiny
DOJI/TINY := needs a configured size threshold; else UNRESOLVED_TINY  [ENGINEERING]
isMaru(x,d) := (STANDARD_MARU or (allowSpecial and SPECIAL_MARU_x)) and dir(x)=d
```

**Contextual size** (needed for "big"): keep ranges of the previous 5 bars that were
Standard/Special Maru (excluding current). `ref5 = max(those 5 ranges)` [default] or mean
[research].
```
BIG_CONTEXT   := 5 refs exist and R >= 0.70 * ref5   // rejects ~65% per assignments
ADVANCED_SIZE := 5 refs exist and R >= 0.50 * ref5   // for 3-candle patterns
```
If <5 refs → `INSUFFICIENT_CONTEXT` (do not shorten lookback in strict mode).

### b2. Valid pullback pattern (2-candle) [COURSE]

`d` = intended pullback direction (counter to pulse). All rules mirror UP/DOWN. Using DOWN
pullback (in an uptrend) as example; `mid1` = midpoint of candle a.

```
TWO_MARU:  isMaru(a,DOWN) and isMaru(b,DOWN)
           and close_b < close_a               // 2nd closes farther in dir
           and R_b >= 0.70 * R_a               // 2nd range >= 70% of 1st
           // NO max-size cap on 2nd candle

BIG+SAME_COLOR:  isMaru(a,DOWN) and BIG_CONTEXT(a) and dir_b=DOWN
                 and high_b < mid1              // 2nd stays on directional half
                 // NO 30% cap here

BIG+OPP_SMALL:   isMaru(a,DOWN) and BIG_CONTEXT(a) and dir_b=UP
                 and R_b < 0.30 * R_a           // opposite candle is small
                 and high_b < mid1
```
UP pullback mirrors: `close_b>close_a`, `low_b>mid1`, `low_b>mid1`.
`allowSmallBeforeBig` (default off) permits assignment-8.10 reversed ordering. [ENGINEERING]

**A pattern is only a CANDIDATE.** It becomes a *structural* pullback only after the next
pulse produces a valid breakout of the recent extreme (§b4).

### b2a. PAC — repair one failed candle [COURSE]

Count failed candles by grouping predicates per candle (many errors on one candle = 1 fail).
- 0 failed → pattern confirms on bar b.
- 1 failed → PAC pending. Inspect next **4** closed bars; confirm when a bar **closes beyond
  the original 2-candle extreme** in `d` (`close > patternHigh` for UP, `< patternLow` for
  DOWN). Confirming bar may be a normal candle. Signal timestamp = confirmation bar; anchor
  stays the original pair.
- 1 failed and no confirm within 4 bars → FAILED → seed a range.
- 2 failed → ordinary PAC prohibited → range/noise unless an advanced 3-candle case applies.

### b2b. Advanced 3-candle exceptions [COURSE, some JUDGMENT]

All confirm on candle `c`, never a/b. `extrema(a,b)` = combined high/low of a & b.
```
A: isMaru(a,d) & long-tail PINBAR(b) & isMaru(c,d) & ADVANCED_SIZE(a)&ADVANCED_SIZE(c)
   & c closes beyond extrema(a,b) in d          // "long tail" = JUDGMENT threshold
B: bigPinbar(a) & b closes near directional edge of a & isMaru(c,d)&ADVANCED_SIZE(c)
   & c closes beyond extrema(a,b)                // "big"/"near" = JUDGMENT
C: NORMAL(a)&NORMAL(b)&isMaru(c,d) & ADVANCED_SIZE(a,b,c) & c closes beyond extrema(a,b)
```

### b3. Valid breakout [COURSE]

Reference = recent extreme, current key, range edge, or POI. Direction `d`.
```
TWO_MARU breakout:  both closes strictly beyond reference in d, and R_b>=0.70*R_a
BIG+SECOND breakout: pullback geometry holds AND >30% of the FIRST candle's BODY lies
                     beyond the line:
   bullPenetration = max(0, close - max(open, line)) / B  > 0.30
   bearPenetration = max(0, min(open, line) - close) / B  > 0.30
BREAKOUT PAC: same 4-bar/1-failed recovery logic as b2a, vs the reference.
```
NB: body-penetration % (breakout) and total-range % (pullback size) are **different
measurements** — do not mix them.

**Fake breakout:** excursion that fails validation. It does NOT move structure; it expands
the range's relevant boundary to the farthest fake extreme. Future escapes must clear the
*latest* boundary, not the original line.

### b4. Swing / structural-extreme tracking (the course's "pivot" substitute)

**The course has no N-bar fractal.** Swings emerge from the pulse/pullback state machine (see
`MarketStructure_Course.pine`, lines 104–276, which is the reference algorithm). State:

```
trend ∈ {UNKNOWN, UP, DOWN}
recentHigh / recentLow  (+ bar index) = current structural extreme to be broken
swLow / swHigh          (+ bar index) = running candidate-pullback extreme
pbBearSeen / pbBullSeen = a valid pullback pattern has formed this leg
keyPrice / keyBar       = current key level
```

Per closed bar, in an UPTREND (mirror for DOWN):
1. **Trend change (CHoCH):** if `keyPrice` set and `close < keyPrice-eps` and current bar is
   a valid bearish breakout → flip `trend=DOWN`; the latest distribution high becomes the new
   current key immediately; reset `recentLow`, `swHigh`, pullback flags.
2. **Continuation (BOS):** else if `pbBearSeen` and `close > recentHigh+eps` and valid bullish
   breakout → confirm pulse; archive old key; promote the pullback low (`swLow`) to current
   key; set `recentHigh` to new high; reset `swLow`, `pbBearSeen`.
3. **Extend / detect pullback:** else — while no pullback yet, raise `recentHigh` on new highs
   (and reset swLow). Once a pullback pattern (§b2) forms, set `pbBearSeen=true` and freeze
   `recentHigh`; track the deepest `swLow`. **Do not erase a seen pullback just because price
   wicks above recentHigh without a valid breakout.**

**Bootstrap** [COURSE]: never infer trend from a single HH or LL. Wait for (1) a valid
pullback candidate + (2) a valid opposite-direction breakout, then set trend in the breakout
direction. Until then `trend=UNKNOWN`/`RANGE`.

**Equal extremes** [COURSE]: when several bars share an extreme within `eps`, move the analysis
anchor to the *latest* such bar (same price). Not a breakout.

### b5. HH / HL / LH / LL labeling [COURSE context, ENGINEERING label placement]

The course frames trend as HH+HL (up) / LL+LH (down) but derives labels from the *validated*
structural points above, not raw geometry. Recommended:
- On each confirmed BOS, the new `recentHigh` (up) is a **HH**; the promoted key (`swLow`) is a
  **HL**. Down-trend: new `recentLow` = **LL**, promoted key = **LH**.
- Compare each new structural high to the prior structural high → HH vs LH; each structural low
  to prior → HL vs LL. Do NOT label unvalidated internal wiggles.

### b6. Trend / phase determination [COURSE]

- `UP` while current bullish key is not validly broken downward (HH/HL sequence intact).
- `DOWN` while current bearish key is not validly broken upward.
- `RANGE` when: a pattern fails into a range seed, a fake breakout is active, or trend labels
  flip repeatedly in a small area (→ tell user to go up a timeframe). Straight-wave fallback:
  if a one-way move has ≥1 valid breakout, keep its origin as key; if zero valid breakouts,
  treat the whole move as a large range.

### b7. Range engine [COURSE + ENGINEERING]

Range seeds (high-confidence): expired/failed PAC (use original pattern low/high); invalid
breakout (reference + farthest excursion); big-pinbar followed by close inside it; manual news
range. State `{initialLow/High, finalLow/High, ACTIVE|ESCAPED}`. While ACTIVE: test breakout
patterns vs `finalHigh/finalLow`; on valid escape mark ESCAPED; else expand final boundary only
on fake-breakout excursions (not on every wick). **Pullbacks fully inside an active range are
SUPPRESSED** (no structural function). A range forming *after* a confirmed pullback keeps the
pullback but pushes its distal boundary to the range extreme and weakens the zone.

### b8. Order-block / imbalance / FVG / POI [COURSE fragments — INCOMPLETE, default OFF]

The course teaches these only as an *imported* companion "smart money" method (lesson 12.3),
without a full spec. Faithful fragments:
- **Imbalance / FVG:** a visible 3-candle gap on a pulse wave (candle-1 wick and candle-3 wick
  do not overlap). Marks a "strong zone." No numeric min size given. [JUDGMENT]
- **POI (≈ order block + OTE):** on an imbalanced pulse wave, draw a Fibonacci from the pulse
  low→high (up) or high→low (down); the **61.8%–80%** band is the "discount/premium" target.
  Scan **right-to-left** for the **first opposite-color candle** inside that band — that candle
  (often a small/doji) is the POI. When a valid POI exists it *supersedes* the key level as the
  entry zone. [COURSE-described, JUDGMENT-quantified]

**Recommendation:** implement FVG detection (deterministic 3-candle non-overlap) and the fib
61.8–80% band as *optional overlays* flagged `[COMPANION_METHOD, INCOMPLETE]`. Do NOT enable
POI by default or treat it as core market-structure; allow manual zones.

### b9. Multi-timeframe & market cycle [COURSE]

Default stack: **HTF=H1, BTF=M15, LTF=M5** (roles configurable; M1 for scalping). HTF gives
condition/cycle/targets; BTF gives the setup/entry/stop; LTF gives risk/trailing. At base bar
close time `t`, use only the most recent HTF bar with `closeTime <= t` (never the live HTF bar).
LTF analysis starts at the latest touch/departure from the relevant HTF key/POI — not the whole
chart. **Market cycle:** an LTF trend "ends" when price reaches the opposing HTF key/POI/recent
extreme; then suppress same-direction entries until either a valid breakout through the HTF
obstacle (continuation) or a valid break of the LTF key (reversal). **Stop descending** when HTF
or BTF is ranging/noisy, zones conflict, or the governing HTF zone hasn't been reached.

### b10. Gaps / news [COURSE + ENGINEERING]

`gap(i) := open[i] != close[i-1]` beyond tolerance. Annotate only: `GAP_PRESENT`,
`POST_GAP_LOW_CONFIDENCE` on the next bar; optionally treat an isolated gap as a synthetic
Maru for analysis (never a direct signal). Many gaps / tiny ranges → `LOW_LIQUIDITY_STOP`, go
up a timeframe. News = manual no-trade range; suppress structure inside, apply normal
final-boundary breakout logic.

### b11. TF/2 [COURSE, default OFF]

Validate an unclear HTF pattern from LTF composition. Mapping is contradictory (H1→M30 math vs
H1→M15/M15→M10/M5→M1 practical) → require explicit config. Six cases: 1/2/3/6 direct; 4/5 need
LTF decomposition (case 5 needs two correctly-positioned directional Marubozu in the LTF).

---

## (c) Drawing / Visualization Guidance (Lightweight Charts)

Color semantics: **bullish/uptrend = green** (`color.lime`), **bearish/downtrend = red**.
Neutral/range = gray.

| Element | Primitive | Style |
|---|---|---|
| Structural zigzag (HH/HL/LL/LH) | LineSeries or line primitives connecting confirmed structural points | width 2; segment colored by the trend it belongs to (the leg *leaving* a distribution extreme starts the new trend's color) |
| HH/HL/LH/LL labels | markers / text labels at each structural point | small text above highs / below lows |
| BOS (continuation break) | horizontal dashed line at the broken recent extreme, from its bar to break bar + label "BOS" | trend color; label_up if bullish else label_down |
| CHoCH (key break / trend change) | horizontal dashed line at the broken key + label "CHoCH" | new-trend color; visually emphasize (thicker/solid) |
| Current key level | horizontal dotted line, extended to current bar | trend color; width 1 |
| Old key level | horizontal dotted line, faded/gray | informational (target/risk) |
| Key-level zone (optional) | box/rectangle primitive | mode-dependent (see below); always show mode label |
| Range | shaded rectangle spanning `[finalLow, finalHigh]` over its bars | translucent gray fill |
| FVG / imbalance (optional) | box between candle-1 and candle-3 wicks | translucent; `[COMPANION]` tag |
| POI (optional) | box at the first-opposite candle in 61.8–80% band | yellow; `[COMPANION]` tag |
| Candle-pattern hits (debug) | markers under/over the confirming bar | e.g. "2M", "B+S", "PAC" |

**Zone rectangle modes** (all [ENGINEERING] except structural price):
- `MANUAL_REVIEW` (default): draw only the structural key *price* line, no guessed box.
- `LAUNCH_CANDLE_CLOSE_WICK`: up zone `[launch low, launch close]`; down zone `[launch close,
  launch high]` (mirrors "first down candle high→close" example).
- `EXTREME_TO_MIDPOINT`: up `[pullback/range low, anchor midpoint]`; bearish mirror.

Non-repaint drawing: place BOS/CHoCH/PAC markers at the **confirmation bar's time**, never
backdated to the pattern's origin. Structural lines may extend rightward; snapshot history must
stay auditable.

---

## (d) Parameters & Defaults

```
bodyMaruMin              = 0.70    // Standard Marubozu body ratio
allowSpecial             = true
spBodyMin                = 0.50    // Special Maru body ratio
spEdge                   = 0.90    // Special Maru close location (closeLoc>=0.90 up / <=0.10 dn)
secondMaruRangeMin       = 0.70    // 2nd Maru range vs 1st
oppositeSmallRangeMax    = 0.30    // opposite small candle < 30% of 1st range
breakBodyBeyondMin       = 0.30    // >30% of first BODY beyond break line
contextLookbackMaruCount = 5       // "previous five Marubozu" window
bigVsReferenceMin        = 0.70    // BIG_CONTEXT threshold
advancedVsReferenceMin   = 0.50    // ADVANCED_SIZE threshold (3-candle cases)
pacMaxBarsAfterPair      = 4       // PAC lookahead window
equalityToleranceTicks   = 0       // strict '>' / '<'; >0 = inclusive, low-confidence
contextReferenceMode     = MAX     // MAX (default) or MEAN
allowSpecialAsMaru       = true
allowSmallBeforeBig      = false   // assignment 8.10 extension
minimumRewardRisk        = 2.00    // plan filter (RR>=2)
maxRiskPercent           = 1.00    // 1% per trade; 0.5% for a 2nd test if enabled
zoneMode                 = MANUAL_REVIEW
tf2Enabled               = false
poiEnabled               = false   // companion method, incomplete
// MTF default stack
HTF=H1 ; BTF=M15 ; LTF=M5
// user-supplied optional (all [ENGINEERING], must be flagged):
tinyRangeAtrFraction, longWickRangeFraction, nearEdgeFraction,
gapMinTicks, lowLiquidityGapCount, rangeEscapeBufferTicks, stopBufferTicks
```

Confidence per event: HIGH (strict predicate, full 5-Maru context, no range/cycle conflict);
MEDIUM (PAC with 1 failed constituent, 2nd test); LOW (equality accepted, gap-adjacent,
post-pullback range, partial context, TF/2); UNRESOLVED (missing threshold, ambiguous zone).

---

## (e) Open Questions / Ambiguities (course is vague — flag in UI)

1. **"Big" reference** — average vs max of previous 5 Maru; assignments favor 70% of max.
2. **Doji / tiny candle** — no volatility-normalized size given; needs user threshold.
3. **Pinbar "big" / "long tail"** — body ratio defined (<50%) but size/wick not quantified.
4. **Key-zone rectangle** — structural *price* is clear; universal proximal/distal box is not.
5. **Range detector** — several 2–5 bar examples, no exhaustive Boolean grammar.
6. **Equality at a boundary** — normally strict "beyond"; assignment 9.2 tolerates equality
   as low-confidence.
7. **30% boundaries** — transcripts alternate "<", "≤/from 30%", and "more than 30%".
8. **Same-color 2nd candle** — no explicit 30% cap in the core rule (only opposite-color has it).
9. **Small-before-big ordering** — accepted in assignment 8.10 only.
10. **PAC confirming candle class** — usually any/normal close-beyond; some examples want a
    stronger candle.
11. **Advanced pinbar "mostly beyond" / "near edge" / absorption** — visual judgments.
12. **2nd test** — half-risk vs skip guidance coexist; 3rd test always rejected.
13. **TF/2 mapping** — mathematical halving conflicts with practical mappings; must be configured.
14. **POI / imbalance / FVG** — imported from another course; incomplete → optional/manual only.
15. **Stops/entry price** — no universal tick/ATR buffer or entry-price formula.
16. **CTS** — absent from course vocabulary; do NOT emit. (The .pine's "CTS"/"BOS" label
    strings are idiosyncratic — use continuation=BOS, key-break=CHoCH per the CTK.)
17. **Old/broken keys** — no longer define trend but remain as target/reaction/risk levels.
18. **Lesson 18.1** — no transcript available; Section 17 absent from the course entirely.

---

### Implementation order (recommended)
Strict single-timeframe closed-bar event engine (candle classes → 2-candle patterns → PAC →
breakout → pulse/pullback state machine + key levels) → range state → closed-bar MTF context →
optional modules (TF/2, POI/FVG, news). The `MarketStructure_Course.pine` state machine
(lines 104–276) is the concrete reference for the swing/BOS/CHoCH logic.
