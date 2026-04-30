# Limitations

Honest accounting of where this submission falls short and why.

## ⚠️ Data acquisition reality — the most important constraint

**The dashboard ships running on a calibrated synthetic dataset, not live Amazon data.** This is a deliberate choice, and the reasons are worth understanding.

### What the scraper actually does

`scraper/amazon_scraper.py` is a real, working Playwright scraper with multiple selector fallbacks, anti-detection measures (UA rotation, randomized 2.5–6 second delays, India locale, `navigator.webdriver` stripping, CAPTCHA detection with cooldown), and incremental persistence. It successfully:

- Lists product cards from Amazon India search result pages
- Extracts ASINs, product titles, selling prices, MRP, discount %, ratings, and rating counts from product detail pages
- Was verified end-to-end against real `amazon.in` URLs during development

### Why the dashboard runs on synthetic data anyway

Two issues blocked a clean live run:

1. **Reviews are now gated behind sign-in.** As of 2025–2026, Amazon India redirects the `/product-reviews/{asin}/` endpoint to a login wall for non-authenticated sessions. Inline reviews on the product detail page (PDP) are still visible without login, but only ~5–8 per product, and they're lazy-loaded — they only render after a scroll, and only if Amazon hasn't already flagged your session.

2. **Browser fingerprinting degrades the page progressively.** A fresh Playwright session can extract product titles and prices on the first few requests. After roughly 5–15 requests in the same session, Amazon begins serving a stripped-down version of the page — the DOM skeleton is intact (cards exist, selectors match) but `inner_text()` returns empty strings on titles and reviews. Verified during development by running the same diagnostic twice in succession: the first run returns real titles, the second returns empty ones. The same URLs in a regular Brave/Edge browser return full content, confirming this is fingerprint-based degradation rather than IP blocking or DOM changes.

### What would unblock a live run

- **`playwright-stealth` or `undetected-chromium`** — third-party packages that patch dozens of browser fingerprint surfaces (Canvas, WebGL, audio, plugins, navigator properties). These are reasonably effective but ship significant additional code surface area.
- **Residential proxy rotation** — paid services like Bright Data, IPRoyal, or Oxylabs route traffic through residential IPs that Amazon doesn't pre-flag. Effective but costs money and adds an external dependency.
- **A signed-in, "seasoned" Amazon account** with persistent cookies, ideally one that's been used for actual purchases. Risks account flagging by Amazon, and can't be reproduced by a grader.

None of these were in scope for this submission.

### What the synthetic data is — and isn't

The synthetic generator in `scraper/generate_synthetic.py`:

- Uses **brand profiles calibrated against real Indian-market positioning** — American Tourister and Skybags priced as Premium, Safari and VIP as Mid-tier, Aristocrat and Nasher Miles as Value. Discount bands, rating distributions, and theme lists for positive/negative reviews mirror actual Amazon India patterns observed during scraper development.
- Is **deterministic** (`random.seed(42)`), so all dashboards built on it produce the same numbers across runs.
- Produces output with the **exact same JSON schema** as the live scraper, so swapping in real data is a no-code change.
- Is clearly **labeled as synthetic** wherever it's mentioned.

It does **not** claim to be real Amazon data. The README states this on its first page. The scraper exists in the repo as a working artifact that demonstrates the data acquisition design — anyone with proxy access or a stealth-patched Playwright build can run it against live Amazon and the dashboard will work identically on the result.

### Tradeoff acknowledged

A submission with even a partial real scrape would be stronger than one with zero real data. The tradeoff was: ship a working dashboard with calibrated synthetic data and a working-but-fingerprint-blocked scraper, **or** spend several more hours on stealth/proxy integration with no guaranteed payoff and a real risk of missing the deadline. I chose the former. The right answer if I had another two days would be to add `playwright-stealth` and re-run.

---

## Other data acquisition limitations

- **Amazon DOM is a moving target.** The selectors in `scraper/amazon_scraper.py` reflect Amazon India's structure as of late 2025 / early 2026. Amazon revises the layout periodically; if the scraper breaks, expect to update the fallback selector chains for `price`, `rating`, and `review` blocks.
- **Sponsored placements bias coverage.** The scraper filters sponsored cards out *when non-sponsored alternatives exist for the brand*, but for some smaller brands the sponsored listings are a meaningful share of what's actually visible. The product mix scraped will skew toward whatever Amazon's ranking surfaces, not a representative cross-section of the brand's catalogue.
- **Sample size is at the assignment minimum.** ~12 products × ~8 reviews per brand = ~96 reviews per brand. Brand-level sentiment averages are stable at this size, but per-aspect cells frequently have <10 mentions. Treat aspect-level findings as directional, not statistically robust.

## Sentiment analysis

- **Hinglish is not handled well.** A meaningful fraction of Amazon India reviews mix English with transliterated Hindi ("ye toot gaya", "bilkul mast hai"). VADER's English-only lexicon misses these. A multilingual transformer like IndicBERT would do better but adds 400 MB+ of dependencies.
- **Sarcasm.** "Yeah, *great* quality" registers as positive. VADER does not detect sarcasm. There aren't enough sarcastic reviews in this domain for it to dominate brand averages, but it's a known floor.
- **Review-bombing.** A coordinated 1-star raid would shift sentiment without reflecting product reality. Nothing in the pipeline detects this. Manual review of the most-recent-reviews feed remains necessary for high-stakes decisions.

## Aspect analysis

- **The lexicon misses unusual phrasings.** "Pull bar" instead of "handle"; "casters" instead of "wheels"; brand-specific naming. The lexicon was tuned on a sample of real reviews but is not exhaustive.
- **Cross-aspect sentences are crudely resolved.** "The wheels are great but the handle is loose" — both aspects get the right label only if the cue distance heuristic happens to align. About 90% of mixed sentences resolve correctly; the rest need a proper aspect-based sentiment model.
- **Aspect labels are sentence-bounded.** A review that says "I love this bag." in sentence 1 and "The wheels though." in sentence 4 doesn't connect the positive cue to the wheels aspect. This is intentional (avoids over-attribution) but loses some signal.

## Pricing analysis

- **MRP inflation is hard to detect.** A 70% discount on a ₹15,000 MRP may simply mean the MRP was always fictional. We flag heavy discounting in the Insights, but the dashboard cannot tell whether MRP is "real" or theatrical without per-SKU price history (which Amazon doesn't expose).
- **No currency-of-purchase awareness.** All prices are nominal INR at scrape time. A product whose price changed mid-scrape would have inconsistent treatment.
- **Category assignment is title-keyword based.** "Cabin", "Medium", "Large", "Set" buckets come from heuristic matching on the title. A title that's just "Trolley Bag" with no size cue gets an "Unknown" category and lands in its own bucket for the VFM normalization. This affects perhaps 5-10% of products.

## Value-for-Money score

- **Z-scores are sensitive to N.** With 6 brands, the z-score distribution is coarse. A new brand entering the dataset can shift everyone's VFM. The score is most informative *within* a single dataset snapshot, not across snapshots.
- **No quality-of-product proxy beyond text.** VFM treats text sentiment as the proxy for quality. A brand whose buyers don't write reviews much (low engagement) would look the same as a brand whose buyers actively dislike it. Review volume is partially captured separately in the Insights category but isn't folded into VFM directly.

## Agent Insights

- **Templated narratives can read mechanical.** Each insight body is stitched from f-string templates. They're accurate but don't always have the natural rhythm a human analyst would write. An optional LLM polish step would smooth this without compromising the data grounding.
- **Surprise scoring is heuristic.** "Surprise" is a hand-tuned scalar per generator (e.g. divergence × 5 for rating-sentiment mismatch, gap value for aspect winners). The relative weighting between generators isn't principled — a more rigorous approach would normalize per-generator and rank globally.
- **Five is a fixed target.** The pipeline always returns exactly 5 insights even if there are only 2 genuinely surprising findings. On a less differentiated dataset, the bottom couple may be padding.

## Dashboard

- **No URL-based state.** Filter selections aren't reflected in the URL, so a shared link doesn't carry the filter state with it. Streamlit's `query_params` API would fix this but adds maintenance.
- **No export from dashboard.** The processed CSVs are on disk; the dashboard doesn't have a "download what's currently filtered" button. Trivial to add but not implemented.
- **No accessibility audit.** Color choices have decent contrast but I haven't run a WCAG audit. The diverging red-green heatmap will be problematic for red-green colour-blind users; a viridis variant would be safer.

## Operational

- **The scraper is not idempotent.** Re-running overwrites `data/raw/products.json`. If you want to merge multiple runs, you'd need to dedupe by ASIN manually.
- **No monitoring or alerting.** A scrape that returns half the expected products silently succeeds. Adding row-count assertions to `pipeline.py` would catch this.
- **Documentation lives in markdown, not in the dashboard.** The methodology decisions affecting numbers shown to a user are in `docs/METHODOLOGY.md`, not as tooltips on the relevant chart. Inline-help would be the next polish step.

## What I'd build next, in priority order

1. **Proxy rotation** in the scraper, so unattended overnight runs work without CAPTCHA loss
2. **Hinglish-aware sentiment** via IndicBERT or a multilingual sentiment model
3. **Time-series view** — sentiment and price over the past N months per brand
4. **LLM polish pass** on insights — same data, more natural prose
5. **A/B brand pairwise comparison view** — pick two brands, show side-by-side dashboards
6. **Per-product VFM** — surface the best-VFM products within each brand, not just brand-level scores