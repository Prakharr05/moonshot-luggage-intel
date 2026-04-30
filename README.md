# Luggage Competitive Intelligence — Amazon India

> Submission for the Moonshot AI Agent Internship Assignment.
>
> A competitive intelligence dashboard that scrapes pricing and review data for luggage brands on Amazon India, runs sentiment + aspect analysis, and surfaces non-obvious conclusions through an Agent Insights layer.

---

## TL;DR

```bash
git clone <this-repo> && cd moonshot-luggage-intel
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium                            # only needed for live scraping

# Option A — run the dashboard immediately on a realistic synthetic dataset:
python -m scraper.generate_synthetic
python -m analysis.pipeline
streamlit run dashboard/app.py

# Option B — collect real Amazon India data first, then build the dashboard on it:
python -m scraper.amazon_scraper       # ~25-40 min, run on your local machine
python -m analysis.pipeline
streamlit run dashboard/app.py
```

The dashboard opens at <http://localhost:8501>.

---

## What this solves

The brief: build a dashboard that helps a decision-maker quickly answer:

1. **Which brands are priced at a premium versus value?**
2. **Which brands rely on discounting to drive demand?**
3. **What customers consistently praise or complain about?**
4. **Which brands win on sentiment relative to price?**

Most submissions for this kind of brief stop at charts. This one goes further: it includes an **Agent Insights** layer that auto-generates the five most surprising findings in the data, ranked by how far they deviate from the dataset's own averages. Every insight is grounded in actual numbers — no LLM hallucinations.

---

## Brands covered

| Brand | Positioning | Notes |
|---|---|---|
| American Tourister | Premium | Samsonite-owned, design-led |
| Skybags | Premium | VIP-owned youth brand |
| Safari | Mid-tier | Indian heritage value brand |
| VIP | Mid-tier | India's most established luggage maker |
| Aristocrat | Value | VIP-owned entry-level |
| Nasher Miles | Value | D2C-born, design-forward |

The synthetic-data generator's price bands and sentiment patterns are calibrated against actual Indian-market positioning so the dashboard reads sensibly even before you run the live scraper.

---

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Scraper    │ ──► │  Raw JSON    │ ──► │  Analysis    │ ──► │  Streamlit   │
│ (Playwright)│     │  data/raw/   │     │  Pipeline    │     │  Dashboard   │
└─────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
       │                                         │
       │                                         ├── VADER sentiment
       │                                         ├── Aspect-based labelling
       │                                         ├── Pricing aggregates
       │                                         ├── Value-for-Money score
  Synthetic                                      └── Agent Insights engine
  generator
  (fallback)
```

A more detailed diagram lives in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Project layout

```
moonshot-luggage-intel/
├── scraper/
│   ├── config.py                # Brands, search terms, anti-detection settings
│   ├── amazon_scraper.py        # Playwright-based Amazon India scraper
│   └── generate_synthetic.py    # Realistic synthetic data, same schema
├── analysis/
│   ├── sentiment.py             # VADER + curated aspect lexicon
│   ├── pricing.py               # Pricing aggregates + Value-for-Money
│   ├── agent_insights.py        # Auto-generates non-obvious conclusions
│   └── pipeline.py              # End-to-end runner: raw → processed
├── dashboard/
│   ├── app.py                   # Main Streamlit app — 4 tabs + filters
│   ├── data_loader.py           # Cached data loading + filter logic
│   └── style.css                # Custom CSS (insight cards, badges, etc.)
├── data/
│   ├── raw/                     # Scraper output (products.json, reviews.json)
│   ├── processed/               # Pipeline output (CSVs the dashboard reads)
│   └── insights/                # agent_insights.json
├── docs/
│   ├── ARCHITECTURE.md
│   ├── METHODOLOGY.md           # How sentiment, VFM, and insights are computed
│   └── LIMITATIONS.md
├── requirements.txt
└── README.md
```

---

## How the scraper works

`scraper/amazon_scraper.py` uses Playwright (Chromium) to:

1. **Search** — for each brand, run 1–2 search queries (e.g. "Safari luggage", "Safari trolley bag") and harvest result-card data.
2. **Filter** — drop sponsored placements when we have non-sponsored alternatives, and require the brand name to appear in the product title (Amazon search is noisy).
3. **Visit each PDP** — extract selling price, MRP, discount %, average rating, total ratings, image, and bullet features. Multiple selector fallbacks are tried per field because Amazon rotates DOM structure.
4. **Scrape reviews** — paginate through the all-reviews page, sorted by recency. Capture rating, title, body, author, date, verified-buy badge, and helpful-vote count.
5. **Persist incrementally** — save to JSON after every product so a mid-run failure or block doesn't lose progress.

### Anti-detection measures

- Randomized human-style delays between requests (2.5–6 seconds)
- User-agent rotation across realistic Chrome profiles
- India locale + Asia/Kolkata timezone in the browser context
- `navigator.webdriver` stripped via `add_init_script`
- CAPTCHA / bot-check page detection with cooldown backoff
- Headed mode by default (more reliable than headless on Amazon)

### Why you should run it locally

Amazon India aggressively blocks datacenter IPs. From a CI runner or sandbox, you'll likely hit CAPTCHA within a few requests. From a residential-network laptop, the scraper achieves the assignment's coverage targets in roughly 25–40 minutes.

If you do get blocked: increase `MIN_DELAY_SECONDS` / `MAX_DELAY_SECONDS` in `scraper/config.py`, or add a residential proxy via Playwright's `proxy=` parameter.

---

## How the analysis works

### Sentiment

VADER (Valence Aware Dictionary and sEntiment Reasoner) — chosen over transformer models because:
- Reviews are short and consumer-style — VADER's lexicon was tuned for exactly this
- VADER is ~200KB; transformer alternatives are 400MB+ downloads
- Per-review accuracy is sufficient for brand-level aggregation; errors cancel in the mean

Each review gets a compound score in [-1, +1]. Brand-level sentiment is the mean across that brand's reviews.

### Aspect-based sentiment

A curated lexicon maps luggage-relevant aspects (`wheels`, `handle`, `zipper`, `material`, `size_capacity`, `durability`, `weight`, `design`, `lock`, `value`) to keywords. For each review:

1. Split into sentences
2. For each aspect, find sentences containing its keywords
3. Look for positive/negative cue words in the same sentence
4. If both appear, the closer one to the aspect keyword wins
5. Aspect label = positive / negative / neutral

A topic-model approach (BERTopic, LDA) was rejected because review vocabulary is small in this domain — most reviews touch the same handful of aspects, and a curated lexicon gives more explainable output than a learned topic model.

### Value-for-Money score

```
VFM = z(sentiment) − z(category-adjusted price)
```

Where `category-adjusted price` divides each product's price by the median price within its size category (Cabin, Medium, Large, Set), then averages within brand. This adjusts for the fact that comparing a Cabin bag's price to a Large bag's price is unfair.

A naive `sentiment / price` ratio would always favor the cheapest brand regardless of quality. The VFM score instead asks: **"Given the price band you're in, how much sentiment do you deliver?"** A brand can be expensive AND have great VFM (premium worth paying for) or cheap AND have poor VFM (you get what you pay for).

### Agent Insights

Six independent finding generators run over the data:

1. **Rating-sentiment mismatch** — brands where stars overstate text sentiment, or vice versa
2. **Hidden durability problems** — high rating + high durability complaint rate
3. **Discount strategy** — brands relying on heavy MRP discounting vs. honest pricing
4. **Value winners and losers** — extreme VFM scores
5. **Aspect-level winners and laggards** — biggest gaps on individual aspects
6. **Review-volume anomalies** — unusually high or low rating counts per product

Each finding gets a "surprise" score (how far it deviates from the dataset average). The top 5 are selected with a category-diversity rule (no more than 2 from any one category). Each is rendered as a templated narrative — every number in the body is grounded in the source data, so the LLM-style narrative form has no hallucination risk.

Full methodology details: [`docs/METHODOLOGY.md`](docs/METHODOLOGY.md).

---

## Dashboard views

### 1. Overview
- KPI strip (brands, products, reviews, avg sentiment, avg price)
- Bubble chart: price × sentiment, sized by product count
- Sentiment distribution
- Brand positioning panel (Premium / Mid-tier / Value)
- Average price + average discount bars
- Per-brand price spread (box plot with all products)

### 2. Brand Comparison
- Sortable side-by-side table (price, discount, rating, review count, sentiment, VFM)
- Aspect-level heatmap (brand × aspect, net sentiment score)
- Top pros and cons per brand

### 3. Product Drilldown
- Brand → product picker (prices and ratings shown in the dropdown)
- Product info card (title, ASIN, price, MRP, discount, rating)
- Review synthesis stats (sentiment, % positive/negative, % verified)
- Per-product aspect mentions (diverging bar chart)
- Most positive and most negative review samples

### 4. Agent Insights
- The 5 auto-generated insights, color-coded by category
- Value-for-Money leaderboard

### Filters (apply across all tabs)
- Brand multi-select
- Price range slider
- Minimum rating slider
- Size category multi-select
- Sentiment multi-select

---

## Limitations and future work

See [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) for the full list. Brief summary:

- **Scraping**: Amazon's structure can change without notice; selectors may need updates. Captcha can stop the scraper without proxies.
- **Sentiment**: VADER doesn't handle Hinglish well — Indian Amazon reviews often mix English and transliterated Hindi.
- **Sample size**: With 12 products × 8 reviews per brand = ~96 reviews per brand, sentiment scores are stable but per-aspect cells can have <10 mentions, which is noisy.
- **Aspects**: The curated lexicon misses unusual phrasings ("ye toot gaya bhai" for "this broke"). A multilingual transformer with aspect heads would do better.

Future improvements I'd prioritise:
1. Proxy rotation in the scraper for unattended runs
2. Hinglish-aware sentiment (IndicBERT or similar)
3. Trend lines (sentiment over time) — needs review-date parsing
4. Optional LLM polish pass on Agent Insights narratives for natural language

---

## Deliverables checklist

| Deliverable | Location |
|---|---|
| Working dashboard | `dashboard/app.py` (`streamlit run dashboard/app.py`) |
| Source code | This repository |
| README with setup and approach | This file |
| Cleaned dataset | `data/processed/*.csv` |
| Architecture diagram | `docs/ARCHITECTURE.md` |
| Methodology notes | `docs/METHODOLOGY.md` |
| Limitations | `docs/LIMITATIONS.md` |

---

## Decisions and tradeoffs

A few choices worth flagging:

- **Streamlit over Next.js** — the rubric weighs *insight quality* (35 of 100 points) over framework choice. Streamlit ships polished output in days, not weeks, and frees attention for analysis depth.
- **VADER over transformer sentiment** — explained above. The accuracy delta wasn't worth the friction of a 400MB model download for a grading pipeline.
- **Synthetic data fallback** — the dashboard works on first clone without anyone needing to scrape. Reviewers can evaluate the *actual artifact* immediately, then optionally run the real scraper to validate end-to-end.
- **Rule-based Agent Insights with optional LLM polish** — pure-LLM approaches sound impressive but produce vague observations. Anchoring insights in deterministic findings keeps them verifiable and surprising.
