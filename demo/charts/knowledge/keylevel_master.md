# Key Level Master Spec — "NCI Trading" (Udemy: keylevel)

Distilled from ~30 lesson transcripts. This course's "key levels" are almost
entirely **Supply & Demand (S/D) zones** (price *bands*), supported by
market-structure swing points, break-and-retest, and multi-timeframe confluence.
Classic single-line Support/Resistance is taught but explicitly deprecated by the
instructor in favor of zones. Everything below is written to run on an OHLC bar
array `{time, open, high, low, close}`.

Notation: `body = |close-open|`, `range = high-low`,
`upperWick = high-max(open,close)`, `lowerWick = min(open,close)-low`.
"Bull/up candle" = close>open; "bear/down candle" = close<open.

---

## (a) Concept Glossary

- **Candle body / tail (wick):** body = open→close distance; tail = body edge→high/low. The two things that matter per candle (7.1).
- **Marubozu:** big body, tiny tails → strong one-side pressure (buying if up, selling if down).
- **Pinbar:** small body + one long tail → strong pressure in one direction but with opposition present. Course "reference standard": body ~30% of total length (ambiguous — see §e).
- **Doji / indecision:** ~no body, small tails → balance. Ignored for signals but *included* inside a base.
- **Engulfing:** two opposite marubozu; the second body must be **≥25% bigger** than the first body (i.e. `body2 ≥ 1.25·body1`) to confirm reversal (7.2, 9.1).
- **Morning/Evening star, Tweezer (≥2 pinbars), Mopping, Three soldiers, Inside bar:** other confirmation patterns (7.2). Course mainly uses engulfing, pinbar, marubozu, double top/bottom.
- **Market structure:** Uptrend = Higher High + Higher Low (HH/HL); Downtrend = Lower Low + Lower High (LL/LH) (9.1, 7.2).
- **Valid pullback / valid break (17.1):** requires **two consecutive marubozu**, OR one big marubozu followed by a small pinbar/doji. A big marubozu followed by a big *opposite* pinbar = **invalid** pullback (just a range).
- **Key level:** the swing extreme (lowest low for an uptrend leg / highest high for a downtrend leg) that holds the buying/selling pressure — in practice drawn as the S/D zone that created that swing (17.1, 10.4).
- **Supply zone:** band where sellers concentrate (above price). **Demand zone:** band where buyers concentrate (below price). S/D is a **RANGE/band**, unlike support/resistance which is a single line (9.8).
- **Reversal vs Continuous zone:** reversal = down-wave→base→up-wave (demand) / up→base→down (supply); continuous = base *within* an ongoing trend. **Only trade reversal zones** (9.2).
- **Base:** cluster of indecision/doji candles between the two impulse waves.
- **Imbalance / FVG (18.1):** gap between candle-1 and candle-3 of a 3-candle impulse (candle3.low > candle1.high for bullish; candle3.high < candle1.low for bearish). Its presence marks a strong zone.
- **POI (Point of Interest):** the 61.8%–80% retracement band of the impulse leg — a refined entry zone (18.1).
- **Safety zone:** the S/D band doubled in width, used for stop placement when the raw zone is thin (9.1, 9.7).
- **Obsolete zone:** old / already-broken / unconfirmed weak zone — ignore (9.11).

---

## (b) Core Detection Algorithms

### 1. Candle classifier (foundation for everything)
For each bar compute body, range, wicks. Classify:
- `marubozu` if `body/range ≥ MARU_BODY` (default **0.70**) and `max(upperWick,lowerWick)/range ≤ 0.30`.
- `doji` if `body/range ≤ DOJI_BODY` (default **0.10**).
- `pinbar` if not marubozu and one wick dominates: `max(wick)/range ≥ PIN_WICK` (default **0.60**) and body on the opposite end. Bull pinbar = long lowerWick; bear pinbar = long upperWick.
- else `normal`.

### 2. Swing points (pivots)
A **swing high** at bar i = `high[i]` is the max of the window `[i-L, i+L]`; **swing low** analogous. Default **L = 2** (needs 2 bars each side to confirm; a level only "prints" after L bars have closed → good for live charts). Track swings in time order to derive structure (HH/HL/LH/LL).

### 3. Impulse + zone detection (the S/D engine)
Walk swings. A **demand zone** forms at a swing low where: a down-move (impulse leg) → optional base → up-move that qualifies as a *valid break* (rule §a). Symmetric for **supply** at a swing high. Steps:
1. Find swing low `S`.
2. Look right for the first **up impulse leg**: ≥1 up marubozu (or 2 consecutive up candles) whose combined move ≥ `IMPULSE_MIN` × ATR (default **1.5×ATR(14)**).
3. Identify the **base** = contiguous doji/indecision bars between the down leg's last candle and the up leg's first strong candle (0..N bars).
4. Draw the band per the case table (§c). Store `{type:'demand', top, bottom, originTime, touches:[], broken:false}`.

### 4. Support/Resistance line detection (secondary, legacy)
Cluster swing highs (resistance) / swing lows (support) whose prices lie within `CLUSTER_TOL` of each other; a valid line needs **≥2** (ideally 3) touches. Render as a single horizontal line. Course notes these get *missed* on lower TFs due to noise — prefer zones.

### 5. Touch / test counting
A bar "touches" a zone if `[low,high]` intersects `[bottom,top]`. Count discrete touches (debounce: require the price to leave the zone by ≥ half the zone height before a new touch counts). More touches held = tested; a touch that then reverses strongly = confirmation.

### 6. Break & flip
Zone is **broken** when a candle **closes** beyond it (demand broken if `close < bottom`; supply if `close > top`) — course requires a *close*, not just a wick (8.3). On break: mark `broken:true`. A broken supply that price retests from above becomes **demand** (support↔resistance / S↔R flip, 9.8), and vice-versa. Retest + confirmation candle = entry.

---

## (c) Zone drawing case table (band boundaries)

Let the impulse's first breakout candle be `C1` (first strong candle leaving the base), and `ext` = the swing extreme (lowest low for demand / highest high for supply).

| Case (9.3–9.7) | Demand band | Supply band |
|---|---|---|
| **General / no base** | `close(C1)` → `ext(low)` | `close(C1)` → `ext(high)` |
| **1st candle too big** | `close(C1)` → `ext(low)` (same as general) | `close(C1)` → `ext(high)` |
| **2nd candle too big** | `midpoint(C1)`=`(open+close)/2` → `ext(low)`; or reuse a prior zone | `midpoint(C1)` → `ext(high)` |
| **Long tail (pinbar rejection)** | `low(tail)` → nearest open/close of base | `high(tail)` → nearest open/close of base |
| **Having base** | `min(base bodies)` → furthest close of base (cover *all* doji) | `max(base bodies)`/high → furthest close of base |

If the resulting band is thin, also compute a **safety zone** = band doubled outward (for SL placement) only in the *having-base* cases.

---

## (d) Strength / significance scoring

Course factors for the **strongest** zone (9.10): (1) ≥2 marubozu right after the reversal; (2) price reverses *immediately* (base only 1–2 candles, small base); (3) how *far* price travels away from C1 (departure/imbalance). Plus freshness/recency and imbalance presence (18.1). Ranking of the 5 zone types: strongest = *no-base, 2nd-candle-too-big*; weakest = *1st-candle-too-big*.

Proposed **strength score S ∈ [0,1]** (tune weights):
```
S = 0.25*departure   // (leg size in ATR, capped at ~3) /3
  + 0.20*impulseMaru  // fraction of impulse-leg candles that are marubozu
  + 0.15*baseTight    // 1 if base ≤2 bars, →0 as base grows to ~8
  + 0.15*imbalance    // 1 if FVG present, else 0
  + 0.15*freshness    // 1 if untested, minus 0.25 per prior touch, floored 0
  + 0.10*tfWeight     // higher timeframe → higher (M1:0.2 … H4/D1:1.0)
```
Zone types can also map to a static multiplier (strongest 1.0 → weakest 0.4).
Untested + fresh + imbalance + higher-TF ⇒ trade; broken/old/low-score ⇒ discard (obsolete, 9.11). Higher-TF and confluence levels outrank lower-TF ones.

---

## (e) Zone vs line, tolerance, timeframe & confluence

- **Zones, not lines.** The course explicitly prefers bands (deviation allowed) over exact S/R lines (9.8). Default zone height comes from the case table; if you must merge nearby touches into one cluster, merge levels within **CLUSTER_TOL = 0.10%** of price (0.05–0.15% tunable; FX pairs ~10 pips on majors). Merging two zones that overlap → union band.
- **Timeframe selection (10.1):** pick a **trading TF**; **entry TF = trading TF ÷ 3–6**; **trend TF = trading TF × 3–6**. Course default combo for beginners: **trend H4 / trade H1 / entry M15** (or trade M15 / entry M5 / trend H1). Draw key levels on the trend & trading TF; refine entry on the entry TF.
- **Confluence (10.4):** all 3 TFs same direction → high-probability entry.
- **Un-confluence (10.7):** trade if the **two adjacent (one-level-apart) TFs** agree (e.g. M15+H1); skip if the agreeing TFs are far apart (M15+H4) or if the M15 range is too narrow.
- **"Reset chart" recency rule (9.11):** only levels near current price matter; zoom-out until candles are small and only keep zones still visible/near price. Implement as: only surface zones whose origin is within `LOOKBACK` bars (default **300**) and within ~`RECENCY_ATR` (default 20×ATR) of current price.

---

## (f) Drawing / visualization guidance (Lightweight Charts)

- **S/D zone → shaded rectangle/box** (a filled band spanning `[bottom,top]` from `originTime` extending right as a ray to the current bar). Use a custom primitive or two line-series bounding a fill.
  - Demand: green fill (e.g. `rgba(38,166,154,0.15)`) with a slightly darker top/bottom border.
  - Supply: red fill (`rgba(239,83,80,0.15)`).
  - **Untested/fresh:** solid border + full opacity. **Tested (≥1 touch):** thinner/faded. **Broken:** dashed grey border, ~50% faded, or drop.
  - **Flipped:** recolor to its new role (broken supply → demand green) once retested.
- **Support/Resistance (legacy) → single horizontal ray/line-series** at the level; green for support, red for resistance; dashed when broken.
- **Safety zone →** faint outer band (dotted border) drawn around the raw zone.
- **Imbalance/FVG →** thin translucent box on the 3-candle gap (distinct hue, e.g. purple) as a strength annotation.
- **Labels:** text at right edge: type + strength, e.g. `DEMAND ★0.82 (H1)`, `SUPPLY (broken)`. Optionally show touch count.
- **Line width / z-order:** higher-TF and higher-score zones drawn with thicker borders and on top.

---

## (g) Parameters & defaults (single table for the implementer)

| Param | Meaning | Default |
|---|---|---|
| `MARU_BODY` | body/range for marubozu | 0.70 |
| `DOJI_BODY` | body/range for doji | 0.10 |
| `PIN_WICK` | dominant wick/range for pinbar | 0.60 |
| `ENGULF_RATIO` | body2/body1 for engulfing | 1.25 |
| `SWING_L` | bars each side for a pivot | 2 |
| `IMPULSE_MIN` | impulse leg size in ATR | 1.5 |
| `ATR_PERIOD` | ATR lookback | 14 |
| `CLUSTER_TOL` | merge tolerance (% of price) | 0.10% |
| `TOUCH_DEBOUNCE` | fraction of zone height price must exit before re-touch | 0.5 |
| `BREAK_RULE` | break needs candle **close** beyond zone | true |
| `LOOKBACK` | bars scanned back | 300 |
| `RECENCY_ATR` | max distance of zone from price (×ATR) | 20 |
| TF combo | trend / trade / entry | H4 / H1 / M15 |

---

## (h) Open questions / ambiguities

1. **Pinbar 30% rule** (7.1) is stated confusingly ("body above 30% of total length"); a pinbar should have a *small* body + long tail, so I implemented it as "one wick ≥60% of range." Tune against real course chart examples.
2. **Exact ATR/impulse threshold** — the course never gives numbers ("goes quite far", "very strong"); `1.5×ATR` is my inference.
3. **Cluster tolerance / zone-merge %** — never quantified; 0.10% is a starting point, instrument-dependent.
4. **Base length cutoff** for "immediate reversal" — course says "1–2 candles" strong vs "~10 candles" weak; I used ≤2 tight, ≥8 weak.
5. **Strength formula** is entirely my synthesis of the three qualitative factors + freshness/imbalance/TF; the course gives no numeric score.
6. **POI 61.8–80%** and imbalance are from later ("level 2") material referenced in 18.1; treat as an optional refinement layer.
7. Course teaches this discretionarily (naked chart, human judgment) — expect to tune every threshold against backtests.
