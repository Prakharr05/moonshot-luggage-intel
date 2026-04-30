"""
Pricing analysis and Value-for-Money (VFM) scoring.

Methodology for VFM score:
- We want a single number per brand that answers: "Given how much you pay,
  how good is the experience?"
- We define VFM = z(sentiment) - z(price_per_capacity_proxy)
  where price_per_capacity_proxy uses the median price within each size
  category. This adjusts for the fact that comparing a Cabin bag's price to
  a Large bag's price is unfair.
- Sentiment is normalized within the dataset (z-score across brands).
- The result: brands with high sentiment relative to their price band score
  high. A brand can be expensive AND have great VFM (premium worth paying for),
  or cheap AND have poor VFM (you get what you pay for).
- Output: VFM score in roughly [-2, 2], where 0 is "average for its price band".

This is more honest than a naive sentiment/price ratio, which would always
favor the cheapest brand regardless of quality.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def brand_pricing_summary(products_df: pd.DataFrame) -> pd.DataFrame:
    """Per-brand pricing aggregates. Robust to missing prices."""
    df = products_df.dropna(subset=["price"]).copy()
    g = df.groupby("brand")
    summary = pd.DataFrame({
        "product_count": g.size(),
        "avg_price": g["price"].mean().round(0),
        "median_price": g["price"].median().round(0),
        "min_price": g["price"].min().round(0),
        "max_price": g["price"].max().round(0),
        "avg_list_price": g["list_price"].mean().round(0),
        "avg_discount_pct": g["discount_pct"].mean().round(1),
        "avg_rating": g["rating"].mean().round(2),
        "total_ratings": g["rating_count"].sum().astype("Int64"),
    }).reset_index()
    return summary.sort_values("avg_price", ascending=False)


def positioning_label(avg_price: float, all_avg_prices: pd.Series) -> str:
    """Bucket a brand's average price as Premium / Mid-tier / Value.
    Cutoffs are tertiles of the brand-level average prices."""
    q1, q2 = all_avg_prices.quantile([0.33, 0.67])
    if avg_price >= q2:
        return "Premium"
    if avg_price >= q1:
        return "Mid-tier"
    return "Value"


def value_for_money_score(
    pricing_summary: pd.DataFrame,
    sentiment_summary: pd.DataFrame,
    products_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    VFM score per brand. Higher = better experience for the price band.

    Steps:
    1. For each product, compute "category-adjusted price" = price / median(price)
       within its size category. A ₹4000 Cabin bag and a ₹4000 Large bag have
       very different category-adjusted prices.
    2. Average that to get per-brand category-adjusted price.
    3. Z-score sentiment and category-adjusted price across brands.
    4. VFM = z(sentiment) - z(category_adj_price).
    """
    prods = products_df.dropna(subset=["price"]).copy()

    # Category median is computed across ALL brands so within-category
    # comparisons are fair across brands.
    prods["cat"] = prods["category_hint"].fillna("Unknown")
    cat_medians = prods.groupby("cat")["price"].transform("median")
    prods["price_relative_to_cat"] = prods["price"] / cat_medians

    cat_adj_by_brand = prods.groupby("brand")["price_relative_to_cat"].mean()

    df = pricing_summary.merge(
        sentiment_summary[["brand", "sentiment_score"]], on="brand", how="left"
    )
    df["cat_adj_price"] = df["brand"].map(cat_adj_by_brand)

    # Z-scores. Guard against zero std (single-brand case).
    def zscore(s: pd.Series) -> pd.Series:
        std = s.std()
        if std == 0 or pd.isna(std):
            return pd.Series(0.0, index=s.index)
        return (s - s.mean()) / std

    df["z_sentiment"] = zscore(df["sentiment_score"])
    df["z_price"] = zscore(df["cat_adj_price"])
    df["vfm_score"] = (df["z_sentiment"] - df["z_price"]).round(2)

    df["positioning"] = df["avg_price"].apply(
        lambda p: positioning_label(p, df["avg_price"])
    )
    return df.sort_values("vfm_score", ascending=False)


def discount_dependence_index(pricing_summary: pd.DataFrame) -> pd.DataFrame:
    """
    A "discount dependence" indicator: how much a brand relies on heavy
    discounting vs. selling at low MRPs.

    A brand offering 70% off ₹15,000 (selling for ₹4,500) signals different
    strategy than one selling at ₹4,500 with 30% off ₹6,500 — even though the
    take-home price is similar. The first relies on discount theatre.

    Score = avg_discount_pct (higher = more discount-dependent).
    Categorical label is added for easier reading.
    """
    df = pricing_summary[["brand", "avg_discount_pct", "avg_price", "avg_list_price"]].copy()
    df["discount_strategy"] = pd.cut(
        df["avg_discount_pct"],
        bins=[-1, 40, 55, 70, 100],
        labels=["Low discount", "Moderate", "Heavy discount", "Extreme discount"],
    )
    return df.sort_values("avg_discount_pct", ascending=False)
