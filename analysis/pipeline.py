"""
End-to-end analysis pipeline.

Reads raw scraper output, enriches reviews with sentiment + aspects,
computes pricing summaries, value-for-money, and agent insights, and
writes everything to data/processed/ as parquet/CSV for the dashboard.

Re-running this is cheap (a few seconds on the synthetic dataset).
The dashboard reads from data/processed/ — it never re-runs analysis on load.

Usage:
    python -m analysis.pipeline
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from analysis.sentiment import (
    enrich_reviews,
    brand_sentiment_summary,
    aspect_breakdown,
)
from analysis.pricing import (
    brand_pricing_summary,
    value_for_money_score,
    discount_dependence_index,
)
from analysis.agent_insights import generate_insights


RAW_DIR = Path("data/raw")
PROCESSED_DIR = Path("data/processed")
INSIGHTS_DIR = Path("data/insights")


def load_raw():
    products = pd.DataFrame(json.load(open(RAW_DIR / "products.json", encoding="utf-8")))
    reviews = pd.DataFrame(json.load(open(RAW_DIR / "reviews.json", encoding="utf-8")))
    return products, reviews


def run():
    print("Loading raw data...")
    products, reviews = load_raw()
    print(f"  products: {len(products)} rows")
    print(f"  reviews:  {len(reviews)} rows")

    print("\nEnriching reviews with sentiment + aspect labels...")
    enriched = enrich_reviews(reviews)
    print(f"  sentiment label distribution: "
          f"{enriched['sentiment_label'].value_counts().to_dict()}")

    print("\nComputing brand-level summaries...")
    brand_sent = brand_sentiment_summary(enriched)
    print(brand_sent.to_string(index=False))

    print("\nComputing pricing summaries...")
    brand_pricing = brand_pricing_summary(products)
    print(brand_pricing.to_string(index=False))

    print("\nValue-for-money scores...")
    vfm = value_for_money_score(brand_pricing, brand_sent, products)
    print(vfm[["brand", "positioning", "avg_price", "sentiment_score", "vfm_score"]].to_string(index=False))

    discount_table = discount_dependence_index(brand_pricing)

    print("\nAspect breakdown...")
    aspects = aspect_breakdown(enriched)
    print(f"  {len(aspects)} brand-aspect rows")

    print("\nGenerating Agent Insights (top 5)...")
    insights = generate_insights(
        products_df=products,
        enriched_reviews=enriched,
        brand_sentiment=brand_sent,
        pricing_summary=brand_pricing,
        vfm_table=vfm,
        aspect_table=aspects,
        n=5,
    )
    for i, ins in enumerate(insights, 1):
        print(f"  [{i}] {ins.headline}")

    # Persist
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    INSIGHTS_DIR.mkdir(parents=True, exist_ok=True)

    # Aspects column is dict — serialize as JSON for the CSV
    enriched_to_save = enriched.copy()
    enriched_to_save["aspects"] = enriched_to_save["aspects"].apply(json.dumps)
    enriched_to_save.to_csv(PROCESSED_DIR / "reviews_enriched.csv", index=False)

    products.to_csv(PROCESSED_DIR / "products_clean.csv", index=False)
    brand_sent.to_csv(PROCESSED_DIR / "brand_sentiment.csv", index=False)
    brand_pricing.to_csv(PROCESSED_DIR / "brand_pricing.csv", index=False)
    vfm.to_csv(PROCESSED_DIR / "value_for_money.csv", index=False)
    discount_table.to_csv(PROCESSED_DIR / "discount_strategy.csv", index=False)
    aspects.to_csv(PROCESSED_DIR / "aspect_breakdown.csv", index=False)

    insights_payload = [asdict(i) for i in insights]
    (INSIGHTS_DIR / "agent_insights.json").write_text(
        json.dumps(insights_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"\n✓ All outputs written to data/processed/ and data/insights/")


if __name__ == "__main__":
    run()
