# Architecture

## High-level data flow

```
                                                    ┌──────────────────┐
                                                    │   data/raw/      │
                                                    │  ┌────────────┐  │
                            ┌──────────────────┐    │  │products.json│ │
                            │  Amazon India    │ ──►│  └────────────┘  │
                            │  (live scraping) │    │  ┌────────────┐  │
                            │                  │    │  │reviews.json│  │
                            │  Playwright +    │    │  └────────────┘  │
                            │  Chromium        │    └─────────┬────────┘
                            └──────────────────┘              │
                                                              │
                            ┌──────────────────┐              │
                            │   Synthetic      │              │
                            │   generator      │ ─────────────┤
                            │   (calibrated    │              │
                            │   fallback)      │              │
                            └──────────────────┘              ▼
                                                    ┌──────────────────┐
                                                    │  Analysis        │
                                                    │  pipeline        │
                                                    │                  │
                                                    │  ├─ enrich       │
                                                    │  │  (VADER +     │
                                                    │  │   aspects)    │
                                                    │  ├─ pricing      │
                                                    │  │  aggregates   │
                                                    │  ├─ VFM scoring  │
                                                    │  └─ insights gen │
                                                    └─────────┬────────┘
                                                              │
                                                              ▼
                                                    ┌──────────────────┐
                                                    │  data/processed/ │
                                                    │  ┌────────────┐  │
                                                    │  │ 6 CSV files│  │
                                                    │  └────────────┘  │
                                                    │  data/insights/  │
                                                    │  ┌────────────┐  │
                                                    │  │insights.json│ │
                                                    │  └────────────┘  │
                                                    └─────────┬────────┘
                                                              │
                                                              ▼
                                                    ┌──────────────────┐
                                                    │  Streamlit       │
                                                    │  dashboard       │
                                                    │                  │
                                                    │  ├─ Overview     │
                                                    │  ├─ Compare      │
                                                    │  ├─ Drilldown    │
                                                    │  └─ Insights     │
                                                    └──────────────────┘
```

## Layer responsibilities

### 1. Acquisition layer (`scraper/`)

Two interchangeable sources that produce **identical schemas**:

**`amazon_scraper.py`** — Playwright + Chromium against `amazon.in`.
- Async, single browser context per run
- One outer loop over brands; inner loops over search-terms → search-result cards → product detail pages → review pages
- Persists incrementally after every product (so a CAPTCHA midway doesn't lose hours of work)
- Defensive selectors with fallbacks; any single field can be missing without crashing the run

**`generate_synthetic.py`** — Deterministic synthetic generator (`random.seed(42)`).
- Brand profiles encode realistic price ranges, discount bands, rating distributions, and themed positive/negative review templates calibrated to actual Indian-market positioning
- Produces the same JSON shape as the real scraper

The dashboard cannot distinguish between the two sources — they're isomorphic from downstream's perspective.

### 2. Processing layer (`analysis/`)

Stateless functions. Input: raw JSON. Output: enriched CSVs + an insights JSON.

```
sentiment.py      ─┬─►  enrich_reviews()           (per-review compound + aspect dict)
                   ├─►  brand_sentiment_summary()  (per-brand pos/neu/neg shares)
                   └─►  aspect_breakdown()         (brand × aspect net scores)

pricing.py        ─┬─►  brand_pricing_summary()    (per-brand price aggregates)
                   ├─►  value_for_money_score()    (z(sentiment) − z(category-adj price))
                   └─►  discount_dependence_index() (heavy/moderate/light bins)

agent_insights.py ─►  generate_insights()          (top-5 ranked findings)

pipeline.py       ─►  orchestrates all of the above and writes outputs
```

Re-running the pipeline is cheap (~3 seconds on the synthetic dataset). The dashboard never recomputes — it reads the materialized CSVs.

### 3. Presentation layer (`dashboard/`)

Streamlit app. Single source of truth: the CSVs in `data/processed/`.

```
app.py
├─ Sidebar filters (brands, price, rating, category, sentiment)
├─ apply_filters() in data_loader.py — pure dataframe filtering
├─ Tab 1: Overview (KPIs, price-vs-sentiment bubble, distributions)
├─ Tab 2: Brand Comparison (sortable table, aspect heatmap, pros/cons)
├─ Tab 3: Product Drilldown (per-product synthesis, sample reviews)
└─ Tab 4: Agent Insights (5 cards + VFM leaderboard)
```

`@st.cache_data` on `load_all()` so disk reads happen once per session. Filters operate on the cached frames in memory.

## Key design choices

| Decision | Why |
|---|---|
| Synthetic fallback ships in-repo | Reviewer can run the dashboard in 30 seconds without scraping |
| JSON for raw, CSV for processed | JSON preserves nested fields (bullet lists, aspect dicts); CSV is what `st.dataframe` likes |
| Aspects stored as JSON-encoded dicts in CSV | Lets pandas round-trip without losing structure |
| Insights pre-computed, not generated on-page-load | Dashboard load time stays under 2 seconds even on cold start |
| Each insight has structured `evidence` field | Renders as a verifiable footer beneath the narrative — no hidden numbers |
| Hard category cap of 2 in insight ranker | Prevents 5 insights all about discounting; forces topical diversity |

## Failure modes and recoveries

| Failure | Behaviour | Recovery |
|---|---|---|
| Amazon CAPTCHA mid-scrape | Cooldown + retry, then skip product | Run continues; partial data persists |
| Selector breakage (Amazon DOM change) | Field returns null; product still saved | Update fallback selectors in `amazon_scraper.py` |
| Missing processed CSVs at dashboard start | `st.error` with the exact commands to run | One-line fix shown to user |
| Empty filter result set | `st.warning` and stop rendering | User loosens filters in sidebar |
| Single-brand selection (z-score blow-up) | `zscore()` returns zeros if std == 0 | VFM degrades gracefully to "all zero" |
