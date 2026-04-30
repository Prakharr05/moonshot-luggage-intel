"""
Loads processed data for the dashboard. Cached so reruns don't re-read disk.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st


PROCESSED_DIR = Path("data/processed")
INSIGHTS_DIR = Path("data/insights")


@st.cache_data(show_spinner=False)
def load_all():
    """Returns a dict of dataframes + the insights list."""
    products = pd.read_csv(PROCESSED_DIR / "products_clean.csv")
    reviews = pd.read_csv(PROCESSED_DIR / "reviews_enriched.csv")

    # Aspects column was JSON-serialized; parse it back to dict
    if "aspects" in reviews.columns:
        reviews["aspects"] = reviews["aspects"].fillna("{}").apply(
            lambda s: json.loads(s) if isinstance(s, str) else (s or {})
        )

    brand_sentiment = pd.read_csv(PROCESSED_DIR / "brand_sentiment.csv")
    brand_pricing = pd.read_csv(PROCESSED_DIR / "brand_pricing.csv")
    vfm = pd.read_csv(PROCESSED_DIR / "value_for_money.csv")
    discount = pd.read_csv(PROCESSED_DIR / "discount_strategy.csv")
    aspects = pd.read_csv(PROCESSED_DIR / "aspect_breakdown.csv")

    insights_path = INSIGHTS_DIR / "agent_insights.json"
    insights = json.loads(insights_path.read_text(encoding="utf-8")) if insights_path.exists() else []

    return {
        "products": products,
        "reviews": reviews,
        "brand_sentiment": brand_sentiment,
        "brand_pricing": brand_pricing,
        "vfm": vfm,
        "discount": discount,
        "aspects": aspects,
        "insights": insights,
    }


def apply_filters(
    data: dict,
    selected_brands: list[str],
    price_range: tuple[float, float],
    min_rating: float,
    categories: list[str] | None,
    sentiment: list[str] | None,
):
    """Filter the products + reviews dataframes based on sidebar selections.

    Returns a NEW dict with the same keys but filtered subsets.
    """
    products = data["products"].copy()
    reviews = data["reviews"].copy()

    if selected_brands:
        products = products[products["brand"].isin(selected_brands)]
        reviews = reviews[reviews["brand"].isin(selected_brands)]

    products = products[
        (products["price"].fillna(0) >= price_range[0])
        & (products["price"].fillna(0) <= price_range[1])
    ]

    if min_rating > 0:
        products = products[products["rating"].fillna(0) >= min_rating]

    if categories:
        products = products[products["category_hint"].isin(categories)]

    # Reviews are constrained to surviving products
    surviving_asins = set(products["asin"])
    reviews = reviews[reviews["asin"].isin(surviving_asins)]

    if sentiment:
        reviews = reviews[reviews["sentiment_label"].isin(sentiment)]

    out = data.copy()
    out["products"] = products
    out["reviews"] = reviews
    return out
