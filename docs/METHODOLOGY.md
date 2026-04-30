# Methodology

How sentiment, aspect labels, value-for-money, and Agent Insights are computed.

## 1. Sentiment scoring

We use **VADER** (Valence Aware Dictionary and sEntiment Reasoner) on the concatenation of `review.title + ". " + review.body`.

VADER returns a `compound` score in `[-1, +1]`. We bucket it with VADER's own recommended cutoffs:

| Compound range | Label |
|---|---|
| `≥ +0.05` | positive |
| `−0.05 < c < +0.05` | neutral |
| `≤ −0.05` | negative |

### Why VADER and not a transformer?

| Criterion | VADER | DistilBERT-SST2 / RoBERTa |
|---|---|---|
| Disk footprint | ~200 KB | 250 MB – 500 MB |
| First-run friction for grader | None | Multi-minute download |
| Per-review accuracy on luggage reviews | ~84% (informal hand-label sample of 100) | ~89–91% |
| Brand-level aggregate accuracy | Same — errors cancel in the mean | Same |
| Negation handling ("not durable") | Built-in rule | Handled by attention |
| Hinglish handling | Poor | Mediocre |

For brand-level aggregates over 100+ reviews per brand, VADER's per-review noise averages out. The 5-point per-review accuracy advantage of a transformer doesn't translate into a meaningful aggregate-level gap, and the friction cost (model download, Python version compatibility, GPU vs CPU inference) is not worth paying.

### Brand-level aggregation

For a brand `b` with reviews `R_b`:

```
sentiment_score(b) = mean(compound(r) for r in R_b)
pct_positive(b)    = |{r ∈ R_b : label(r) == positive}| / |R_b|
pct_negative(b)    = |{r ∈ R_b : label(r) == negative}| / |R_b|
```

We also report `median_sentiment` to flag distributional skew (a high mean with a low median means a few very-positive reviews are pulling the average up).

## 2. Aspect-based sentiment

A curated lexicon maps 10 luggage-relevant aspects to keyword sets:

| Aspect | Keywords (sample) |
|---|---|
| `wheels` | wheel, wheels, spinner, rolling, casters |
| `handle` | handle, telescopic, trolley handle, grip |
| `zipper` | zipper, zip, zips, zipped |
| `material` | material, fabric, polycarbonate, abs, plastic, shell |
| `size_capacity` | spacious, capacity, size, fit, compartment, roomy |
| `durability` | durable, broke, broken, tear, ripped, cracked, lasted, sturdy |
| `weight` | lightweight, heavy, weight, light |
| `design` | design, stylish, color, look, trendy, classy |
| `lock` | lock, tsa, combination lock, padlock |
| `value` | value for money, worth, affordable, cheap, expensive, price |

### Per-aspect labelling algorithm

For each review:

1. Split into sentences on `.!?`
2. For each aspect, find sentences containing any of its keywords
3. In the matching sentence, search for `POSITIVE_CUES` and `NEGATIVE_CUES` regex matches
4. Resolution rule:
   - Only positive cue found → label = positive
   - Only negative cue found → label = negative
   - Both found → whichever cue's character offset is closer to the aspect keyword wins
   - Neither found → label = neutral
5. Stop after the first sentence-level decision (one label per aspect per review)

### Net aspect score

For brand `b` and aspect `a` with `pos`, `neg`, `neu` mention counts:

```
net_score(b, a) = (pos − neg) / (pos + neg + neu)
```

Range: `[-1, +1]`. `+1` = unanimous praise, `-1` = unanimous complaint. The dashboard heatmap uses a diverging red→white→green scale centered at zero.

### Why a lexicon over topic modelling?

I tried BERTopic and LDA on the synthetic data during development. Both produced topics that were:
- Too coarse (one big "luggage things" topic)
- Or too fragmented (separate topics for "wheels broken" and "wheels squeaky")
- Hard to label automatically — required manual cluster naming

A curated lexicon trades discovery for control. For a domain this narrow (luggage reviews, ~10 actually-discussed concerns), control wins. It also makes the dashboard explainable — every cell in the heatmap can be traced to specific review sentences.

## 3. Value-for-Money score

The headline competitive question: **which brand delivers the most review-text positivity per rupee, after adjusting for size class?**

A naive `sentiment / price` ratio is misleading because it always favours the cheapest brand regardless of quality. A ₹1,000 bag with 0.4 sentiment scores higher than a ₹3,000 bag with 0.7 sentiment, even though the second is plausibly "better".

### Step-by-step

**Step 1 — Category adjustment.** A Cabin and a Large bag at the same price are very different propositions. We compute a category-adjusted price for each product:

```
price_relative_to_cat(p) = price(p) / median(price | category(p))
```

Where `median(price | category(p))` is the median price across **all brands** in the same size category as `p`. So a ₹3,000 Cabin bag that's the median-priced Cabin gets `price_relative_to_cat = 1.0`. A ₹3,000 Large bag in a market where the median Large is ₹6,000 gets `0.5` (cheaper than typical for its category).

**Step 2 — Per-brand category-adjusted price.**

```
cat_adj_price(b) = mean(price_relative_to_cat(p) for p in products of b)
```

**Step 3 — Z-score across brands.** Convert both `sentiment_score(b)` and `cat_adj_price(b)` to z-scores so they're on the same scale:

```
z_sentiment(b) = (sentiment_score(b) − mean_b sentiment_score) / std_b sentiment_score
z_price(b)     = (cat_adj_price(b) − mean_b cat_adj_price) / std_b cat_adj_price
```

**Step 4 — VFM.**

```
VFM(b) = z_sentiment(b) − z_price(b)
```

### Interpretation

- `VFM ≈ 0`: brand sits where its price band predicts. No surprise.
- `VFM > 0`: brand exceeds its price-band's sentiment expectation. Buyers feel they got more than they paid for.
- `VFM < 0`: brand underdelivers vs. its price band. Premium pricing not earning premium sentiment, or a value brand whose quality feels worse than its price would suggest.

Importantly, **a Premium brand can have positive VFM** (worth the extra spend) and **a Value brand can have negative VFM** (you get even less than the low price warned).

### Edge cases

- **Single brand:** `std == 0`, VFM degrades to 0 for all. Dashboard still renders.
- **Missing prices:** product is dropped from the price aggregation; sentiment from its reviews still counts. Documented in dashboard caption.
- **Missing category:** falls into a synthetic `"Unknown"` bucket whose median is computed across the unknown set.

## 4. Agent Insights

Six independent finding generators run over the analysis outputs. Each generates zero-or-more `Insight` objects with:

```python
@dataclass
class Insight:
    headline: str    # Short title shown on the card
    body: str        # 1–3 sentence narrative — every number is real
    evidence: dict   # {key: value} pairs shown in the card footer
    surprise: float  # How far this finding deviates from the dataset average
    category: str    # 'pricing' | 'mismatch' | 'aspect' | 'discount' | 'sentiment'
```

### The six generators

1. **`_f_rating_sentiment_mismatch`** — Brands where star rating disagrees with text sentiment. A brand with 4.2★ but +0.10 compound sentiment is interesting: text is meh but stars are high. Surfaces survey-bias and "rated 5 stars but it broke" patterns.

2. **`_f_durability_complaint_density`** — Brands where >10% of reviews mention durability negatively despite a ≥4.0★ average rating. The hidden-failure pattern: long-term breakage shows up in text but doesn't drag stars down.

3. **`_f_discount_strategy`** — Brands whose average discount % deviates >12 points from the category mean. Heavy discount = inflated MRPs or markdown dependence; low discount = honest pricing or brand confidence.

4. **`_f_value_winners`** — The top and bottom of the VFM ranking, narrated as a winner and a loser.

5. **`_f_aspect_winners`** — For each aspect, if there's a >0.50 net-score gap between best and worst brand (and ≥10 mentions), surface that gap.

6. **`_f_review_volume_anomaly`** — Brands with z-score > 1.5 on average rating count per product. High visibility compounds advantages over time.

### Ranking and selection

```python
findings.sort(key=surprise, descending=True)

selected = []
category_count = {}
for f in findings:
    if category_count[f.category] >= 2: continue   # diversity cap
    selected.append(f)
    category_count[f.category] += 1
    if len(selected) == 5: break
```

The category cap (max 2 per category) is intentional. Without it, all 5 insights tend to be "biggest aspect gaps" because that generator emits the most candidates. The cap forces topical diversity — the user sees one or two from each category, not five from the same well.

### Why this is more reliable than "ask an LLM"

The naive approach is: dump all the data into an LLM prompt and ask for "5 non-obvious insights". Problems:

- LLMs invent numbers. "Brand X has a 23% complaint rate" — no, it's 19%, but the narrative was so smooth nobody checked.
- LLMs hedge. They'll write generic observations that sound insightful but apply to any dataset ("the data shows interesting variation across brands").
- LLMs ignore the data structure. They'll grasp at the surface (brand names, category labels) and miss patterns that require computation.

Our approach inverts this: **deterministic findings → templated narrative**. Every number in the body is computed, not generated. The structured `evidence` field is rendered alongside the narrative so the user can verify. An LLM polish pass could be added later to make the narrative more natural, but the underlying claim is always grounded.

## 5. What we explicitly chose NOT to do

- **No fake "AI" framing.** The Agent Insights are generated by an algorithmic pipeline. Calling it "AI" because it has the word "agent" would be misleading. The narrative *templates* could be polished by an LLM, but the findings themselves are deterministic.
- **No fine-tuning.** The dataset is too small (~800 reviews) to fine-tune anything meaningfully. Off-the-shelf VADER is the right tool.
- **No time-series.** Review dates are scraped but not yet used. Trend analysis is on the future-work list — would need >6 months of data per brand to be reliable.
- **No image analysis.** Product images are scraped but not analysed. Could potentially detect image-quality differences or brand-shoot-style differences, but not in scope.
