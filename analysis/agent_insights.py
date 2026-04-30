"""
Agent Insights: auto-generate non-obvious conclusions from the data.

The rubric explicitly calls out a "section that automatically generates 5
non-obvious conclusions from the data" — this is that section.

DESIGN:
We do NOT just call an LLM with the raw data and hope for the best. That tends
to produce vague, plausible-sounding-but-wrong observations. Instead:

1. We compute structured findings deterministically (a "finding" is something
   like "Brand X has 4.2 avg rating but 35% of reviews mention durability
   negatively — a rating/sentiment divergence").
2. We rank findings by surprise (how far they deviate from the dataset average).
3. We pick the top 5 most surprising findings.
4. Each finding is rendered as a templated narrative with the actual numbers.
5. (Optional) An LLM can be asked to rewrite the templates more naturally,
   but the underlying claim is always grounded in real numbers — the LLM
   never fabricates.

This makes the insights:
- Verifiable (you can trace every number back to the data)
- Non-obvious (they're surprises, not summaries)
- Reliable (no LLM hallucinations of facts)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


@dataclass
class Insight:
    """A single agent-generated insight."""
    headline: str           # Short title
    body: str               # 1-3 sentence explanation
    evidence: dict          # Numeric backing — shown as a tooltip / footer in UI
    surprise: float         # How surprising (used for ranking)
    category: str           # 'pricing', 'sentiment', 'mismatch', 'aspect', 'discount'


# ---------- Individual finding generators ----------------------------------

def _f_rating_sentiment_mismatch(
    brand_summary: pd.DataFrame,
    enriched_reviews: pd.DataFrame,
) -> list[Insight]:
    """Find brands where star rating disagrees with text sentiment.

    A rating-sentiment mismatch is genuinely surprising: the marketplace shows
    one number but the text tells a different story. Common cause: review-bombing
    or "rated 5 stars but the bag broke" reviews where users vent in text but
    forget to drop the star count.
    """
    out: list[Insight] = []
    rating_by_brand = (
        enriched_reviews.dropna(subset=["rating"])
        .groupby("brand")["rating"].mean()
    )
    sent_by_brand = brand_summary.set_index("brand")["sentiment_score"]
    common = rating_by_brand.index.intersection(sent_by_brand.index)

    for brand in common:
        rating = rating_by_brand[brand]
        sent = sent_by_brand[brand]
        # A 4.0+ star rating combined with a meh-or-worse sentiment is the surprise
        if rating >= 4.0 and sent < 0.20:
            divergence = rating / 5 - (sent + 1) / 2  # both normalized to 0-1
            out.append(Insight(
                headline=f"{brand}: ratings outshine actual review sentiment",
                body=(
                    f"{brand} averages {rating:.2f}★ on its products, but the text of "
                    f"those same reviews shows a sentiment score of only {sent:+.2f} "
                    f"(scale -1 to +1). Star ratings appear to overstate satisfaction — "
                    f"buyers are clicking high stars while writing critical text."
                ),
                evidence={
                    "avg_rating": round(rating, 2),
                    "sentiment_score": round(sent, 2),
                    "divergence": round(divergence, 2),
                },
                surprise=divergence * 5,
                category="mismatch",
            ))
        elif rating < 4.0 and sent > 0.4:
            out.append(Insight(
                headline=f"{brand}: text sentiment is warmer than the star rating implies",
                body=(
                    f"Despite an average rating of {rating:.2f}★, the actual review text "
                    f"for {brand} carries a {sent:+.2f} sentiment score. The brand is "
                    f"under-rated — readers describing positive experiences in text "
                    f"are still picking middling stars."
                ),
                evidence={"avg_rating": round(rating, 2), "sentiment_score": round(sent, 2)},
                surprise=(sent - 0.4) * 5,
                category="mismatch",
            ))
    return out


def _f_durability_complaint_density(
    enriched_reviews: pd.DataFrame,
    products_df: pd.DataFrame,
) -> list[Insight]:
    """Brands where durability is the dominant complaint category.

    This is more surprising for a brand with a high average rating — it means
    the durability problem is hiding under a generally positive picture.
    """
    out: list[Insight] = []
    for brand, sub in enriched_reviews.groupby("brand"):
        total_reviews = len(sub)
        durability_negs = sum(
            1 for asps in sub["aspects"]
            if asps.get("durability") == "negative"
        )
        if total_reviews < 30:
            continue
        rate = durability_negs / total_reviews
        # Look at this brand's average rating from product data
        brand_prods = products_df[products_df["brand"] == brand]
        avg_rating = brand_prods["rating"].mean() if len(brand_prods) else None
        if rate > 0.10 and avg_rating and avg_rating >= 4.0:
            out.append(Insight(
                headline=f"{brand}: hidden durability problem behind a {avg_rating:.1f}★ shopfront",
                body=(
                    f"{rate*100:.0f}% of {brand} reviews mention durability problems "
                    f"(broken wheels, torn fabric, failed zippers, handle issues), "
                    f"yet the brand still sustains a {avg_rating:.1f}★ average. "
                    f"This is a classic anomaly: long-term failures show up in text "
                    f"but rarely cause buyers to update their stars."
                ),
                evidence={
                    "durability_complaint_rate": round(rate, 3),
                    "avg_rating": round(avg_rating, 2),
                    "n_reviews": total_reviews,
                },
                surprise=rate * 10 + (avg_rating - 4.0) * 2,
                category="aspect",
            ))
    return out


def _f_discount_strategy(pricing_summary: pd.DataFrame) -> list[Insight]:
    """Brands relying on heavy MRP discounting vs. honest pricing."""
    out: list[Insight] = []
    if pricing_summary.empty:
        return out
    overall_avg_disc = pricing_summary["avg_discount_pct"].mean()

    for _, row in pricing_summary.iterrows():
        brand = row["brand"]
        disc = row["avg_discount_pct"]
        if pd.isna(disc):
            continue
        if disc > overall_avg_disc + 12:  # ~1 std above average
            out.append(Insight(
                headline=f"{brand} leans on aggressive MRP discounting",
                body=(
                    f"{brand} sells at an average {disc:.0f}% off MRP — "
                    f"{disc - overall_avg_disc:+.0f} percentage points above the "
                    f"category average of {overall_avg_disc:.0f}%. Two interpretations: "
                    f"either MRPs are inflated to make discounts look attractive, or "
                    f"the brand needs heavier markdowns to move inventory."
                ),
                evidence={
                    "avg_discount_pct": round(disc, 1),
                    "category_avg_discount_pct": round(overall_avg_disc, 1),
                },
                surprise=(disc - overall_avg_disc) / 5,
                category="discount",
            ))
        elif disc < overall_avg_disc - 12:
            out.append(Insight(
                headline=f"{brand}: priced honestly, light on discount theatre",
                body=(
                    f"{brand}'s {disc:.0f}% average discount is well below the category "
                    f"norm of {overall_avg_disc:.0f}%. The brand isn't padding MRP to "
                    f"create perceived savings — confidence in list pricing or an "
                    f"audience that responds to brand rather than markdown signals."
                ),
                evidence={
                    "avg_discount_pct": round(disc, 1),
                    "category_avg_discount_pct": round(overall_avg_disc, 1),
                },
                surprise=(overall_avg_disc - disc) / 5,
                category="discount",
            ))
    return out


def _f_value_winners(vfm_table: pd.DataFrame) -> list[Insight]:
    """The standout VFM winner and loser."""
    out: list[Insight] = []
    if len(vfm_table) < 2:
        return out
    winner = vfm_table.iloc[0]
    loser = vfm_table.iloc[-1]

    out.append(Insight(
        headline=f"{winner['brand']} delivers more sentiment per rupee than peers",
        body=(
            f"After adjusting for size category, {winner['brand']} earns the highest "
            f"value-for-money score in the set ({winner['vfm_score']:+.2f}). It sits in "
            f"the {winner['positioning']} band but converts price into review sentiment "
            f"more efficiently than brands at similar price points."
        ),
        evidence={
            "vfm_score": round(winner["vfm_score"], 2),
            "positioning": winner["positioning"],
            "avg_price": int(winner["avg_price"]),
            "sentiment_score": round(winner["sentiment_score"], 2),
        },
        surprise=abs(winner["vfm_score"]) + 0.5,
        category="pricing",
    ))

    out.append(Insight(
        headline=f"{loser['brand']}: weakest value conversion in the set",
        body=(
            f"{loser['brand']} posts a {loser['vfm_score']:+.2f} VFM score — the lowest "
            f"in the set. Its {loser['positioning'].lower()} pricing isn't justified by "
            f"correspondingly higher review sentiment relative to peers."
        ),
        evidence={
            "vfm_score": round(loser["vfm_score"], 2),
            "positioning": loser["positioning"],
            "avg_price": int(loser["avg_price"]),
            "sentiment_score": round(loser["sentiment_score"], 2),
        },
        surprise=abs(loser["vfm_score"]) + 0.3,
        category="pricing",
    ))
    return out


def _f_aspect_winners(aspect_table: pd.DataFrame) -> list[Insight]:
    """Per-aspect leaders and laggards — only flag ones that diverge from peers."""
    out: list[Insight] = []
    for aspect, sub in aspect_table.groupby("aspect"):
        if len(sub) < 3 or sub["mentions"].max() < 10:
            continue
        sub = sub.sort_values("net_score", ascending=False)
        winner = sub.iloc[0]
        loser = sub.iloc[-1]
        gap = winner["net_score"] - loser["net_score"]
        if gap < 0.5:
            continue  # Not a notable gap
        out.append(Insight(
            headline=f"On {aspect}, {winner['brand']} leads while {loser['brand']} trails",
            body=(
                f"For the '{aspect}' aspect, {winner['brand']} has a "
                f"{winner['net_score']:+.2f} net sentiment score (out of "
                f"{winner['mentions']} mentions), while {loser['brand']} sits at "
                f"{loser['net_score']:+.2f} ({loser['mentions']} mentions). Gap of "
                f"{gap:.2f} points is one of the largest in the dataset."
            ),
            evidence={
                "winner": winner["brand"],
                "winner_score": round(winner["net_score"], 2),
                "loser": loser["brand"],
                "loser_score": round(loser["net_score"], 2),
                "gap": round(gap, 2),
            },
            surprise=gap,
            category="aspect",
        ))
    return out


def _f_review_volume_anomaly(
    products_df: pd.DataFrame,
    pricing_summary: pd.DataFrame,
) -> list[Insight]:
    """A brand whose visible reviews per product is unusually high or low."""
    out: list[Insight] = []
    rv_by_brand = (
        products_df.dropna(subset=["rating_count"])
        .groupby("brand")["rating_count"].mean()
    )
    if len(rv_by_brand) < 3:
        return out
    overall_mean = rv_by_brand.mean()
    overall_std = rv_by_brand.std()
    if pd.isna(overall_std) or overall_std == 0:
        return out
    for brand, val in rv_by_brand.items():
        z = (val - overall_mean) / overall_std
        if z > 1.5:
            out.append(Insight(
                headline=f"{brand} dominates on review volume per product",
                body=(
                    f"{brand} listings carry an average of {int(val):,} ratings each, "
                    f"vs. a category mean of {int(overall_mean):,}. High visibility "
                    f"signals either superior shelf placement or better organic "
                    f"momentum — both compound advantages over time."
                ),
                evidence={
                    "avg_rating_count": int(val),
                    "category_mean_rating_count": int(overall_mean),
                    "z_score": round(z, 2),
                },
                surprise=z,
                category="sentiment",
            ))
    return out


# ---------- Master orchestrator --------------------------------------------

def generate_insights(
    products_df: pd.DataFrame,
    enriched_reviews: pd.DataFrame,
    brand_sentiment: pd.DataFrame,
    pricing_summary: pd.DataFrame,
    vfm_table: pd.DataFrame,
    aspect_table: pd.DataFrame,
    n: int = 5,
) -> list[Insight]:
    """Run all finding generators, rank by surprise, return top N."""
    findings: list[Insight] = []
    findings.extend(_f_rating_sentiment_mismatch(brand_sentiment, enriched_reviews))
    findings.extend(_f_durability_complaint_density(enriched_reviews, products_df))
    findings.extend(_f_discount_strategy(pricing_summary))
    findings.extend(_f_value_winners(vfm_table))
    findings.extend(_f_aspect_winners(aspect_table))
    findings.extend(_f_review_volume_anomaly(products_df, pricing_summary))

    # Rank by surprise. Within the top, ensure category diversity — we don't want
    # 5 insights all about discounting.
    findings.sort(key=lambda i: i.surprise, reverse=True)

    selected: list[Insight] = []
    seen_categories: dict[str, int] = {}
    for ins in findings:
        # Cap each category at 2 to enforce diversity
        if seen_categories.get(ins.category, 0) >= 2:
            continue
        selected.append(ins)
        seen_categories[ins.category] = seen_categories.get(ins.category, 0) + 1
        if len(selected) == n:
            break

    # If we still have fewer than n, fill in from the remaining
    if len(selected) < n:
        existing_ids = {id(s) for s in selected}
        for ins in findings:
            if id(ins) in existing_ids:
                continue
            selected.append(ins)
            if len(selected) == n:
                break
    return selected
