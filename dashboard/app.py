"""
Moonshot Luggage Competitive Intelligence Dashboard
===================================================

Streamlit app with three primary views:
  1. Overview — high-level snapshot of the entire dataset
  2. Brand Comparison — side-by-side benchmarking
  3. Product Drilldown — single-product deep dive with review synthesis

Plus an Agent Insights section that surfaces 5 non-obvious conclusions.

Run from the project root:  streamlit run dashboard/app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is importable regardless of where Streamlit was invoked.
# Streamlit runs this file as __main__, so the `dashboard` and `analysis`
# packages aren't auto-discovered without this shim.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from dashboard.data_loader import load_all, apply_filters


# =============================================================================
# Page setup
# =============================================================================
st.set_page_config(
    page_title="Luggage Intel · Amazon India",
    page_icon="🧳",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Inject custom CSS
css_path = Path(__file__).parent / "style.css"
if css_path.exists():
    st.markdown(f"<style>{css_path.read_text()}</style>", unsafe_allow_html=True)

# Brand color palette — used consistently across all charts
BRAND_COLORS = {
    "American Tourister": "#dc2626",
    "Skybags": "#2563eb",
    "Safari": "#059669",
    "VIP": "#9333ea",
    "Aristocrat": "#ea580c",
    "Nasher Miles": "#0891b2",
}
SENTIMENT_COLORS = {"positive": "#10b981", "neutral": "#94a3b8", "negative": "#ef4444"}
POSITIONING_COLORS = {"Premium": "#f59e0b", "Mid-tier": "#3b82f6", "Value": "#10b981"}


# =============================================================================
# Load data once
# =============================================================================
try:
    DATA = load_all()
except FileNotFoundError as e:
    st.error(
        "Processed data not found. Run the analysis pipeline first:\n\n"
        "```\npython -m scraper.generate_synthetic   # or run the real scraper\n"
        "python -m analysis.pipeline\n```"
    )
    st.stop()


# =============================================================================
# Sidebar filters
# =============================================================================
with st.sidebar:
    st.markdown("## 🧳 Luggage Intel")
    st.caption("Amazon India · competitive analysis")
    st.markdown("---")
    st.markdown("### Filters")

    all_brands = sorted(DATA["products"]["brand"].unique())
    selected_brands = st.multiselect(
        "Brands",
        options=all_brands,
        default=all_brands,
        help="Filter the entire dashboard to selected brands.",
    )

    price_min = int(DATA["products"]["price"].min())
    price_max = int(DATA["products"]["price"].max())
    price_range = st.slider(
        "Price range (₹)",
        min_value=price_min,
        max_value=price_max,
        value=(price_min, price_max),
        step=100,
    )

    min_rating = st.slider("Minimum rating ★", 0.0, 5.0, 0.0, step=0.1)

    cat_options = sorted(DATA["products"]["category_hint"].dropna().unique())
    selected_categories = st.multiselect(
        "Size category",
        options=cat_options,
        default=cat_options,
    )

    sentiment_options = ["positive", "neutral", "negative"]
    selected_sentiments = st.multiselect(
        "Review sentiment",
        options=sentiment_options,
        default=sentiment_options,
        help="Limits the review-text analyses to the chosen sentiment buckets.",
    )

    st.markdown("---")
    st.caption(
        "Data source: Amazon India product pages and reviews. "
        "Sentiment via VADER; aspect mining via curated luggage lexicon."
    )

# Apply filters
F = apply_filters(
    DATA,
    selected_brands=selected_brands,
    price_range=price_range,
    min_rating=min_rating,
    categories=selected_categories,
    sentiment=selected_sentiments,
)

if F["products"].empty:
    st.warning("No products match the current filter combination. Loosen the filters in the sidebar.")
    st.stop()


# =============================================================================
# Header
# =============================================================================
st.title("🧳 Luggage Competitive Intelligence")
st.markdown(
    "<p style='color:#64748b;font-size:1.05rem;margin-top:-0.5rem;'>"
    "Pricing, sentiment, and aspect-level signals across Amazon India luggage brands."
    "</p>",
    unsafe_allow_html=True,
)


# =============================================================================
# Top-level KPI strip (always visible)
# =============================================================================
kpi_cols = st.columns(5)
with kpi_cols[0]:
    st.metric("Brands tracked", F["products"]["brand"].nunique())
with kpi_cols[1]:
    st.metric("Products", len(F["products"]))
with kpi_cols[2]:
    st.metric("Reviews analyzed", f"{len(F['reviews']):,}")
with kpi_cols[3]:
    avg_sent = F["reviews"]["sentiment_compound"].mean() if not F["reviews"].empty else 0
    st.metric("Avg sentiment", f"{avg_sent:+.2f}", help="VADER compound score, range [-1, +1]")
with kpi_cols[4]:
    avg_price = F["products"]["price"].mean()
    st.metric("Avg price", f"₹{avg_price:,.0f}" if not pd.isna(avg_price) else "—")


# =============================================================================
# Tabs — three primary views
# =============================================================================
tab_overview, tab_compare, tab_drilldown, tab_insights = st.tabs(
    ["📊 Overview", "⚖️ Brand Comparison", "🔍 Product Drilldown", "🧠 Agent Insights"]
)


# -----------------------------------------------------------------------------
# OVERVIEW TAB
# -----------------------------------------------------------------------------
with tab_overview:
    st.markdown("### Market positioning at a glance")

    # Filtered pricing & sentiment summaries (recomputed from F)
    p_summary = (
        F["products"]
        .dropna(subset=["price"])
        .groupby("brand")
        .agg(
            product_count=("asin", "count"),
            avg_price=("price", "mean"),
            avg_discount_pct=("discount_pct", "mean"),
            avg_rating=("rating", "mean"),
        )
        .round(2)
        .reset_index()
    )
    s_summary = (
        F["reviews"]
        .groupby("brand")
        .agg(
            review_count=("review_id", "count"),
            sentiment_score=("sentiment_compound", "mean"),
        )
        .round(3)
        .reset_index()
    )
    overview = p_summary.merge(s_summary, on="brand", how="outer")

    # Bubble chart: price (x) vs sentiment (y), bubble size = product_count
    col_left, col_right = st.columns([2, 1])
    with col_left:
        fig = px.scatter(
            overview,
            x="avg_price",
            y="sentiment_score",
            size="product_count",
            color="brand",
            color_discrete_map=BRAND_COLORS,
            text="brand",
            size_max=55,
            hover_data={
                "avg_discount_pct": ":.1f",
                "avg_rating": ":.2f",
                "review_count": True,
                "avg_price": ":,.0f",
            },
            labels={
                "avg_price": "Average price (₹)",
                "sentiment_score": "Sentiment score (text)",
            },
            height=460,
            title="Price vs review sentiment by brand (bubble = product count)",
        )
        fig.update_traces(textposition="top center", textfont_size=11)
        fig.update_layout(
            plot_bgcolor="white",
            xaxis=dict(showgrid=True, gridcolor="#f1f5f9", zeroline=False),
            yaxis=dict(showgrid=True, gridcolor="#f1f5f9", zeroline=True, zerolinecolor="#cbd5e1"),
            margin=dict(l=20, r=20, t=50, b=20),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "Top-right is the sweet spot: positive sentiment despite premium pricing. "
            "Top-left signals strong value (positive sentiment at low prices)."
        )

    with col_right:
        st.markdown("##### Sentiment distribution")
        sent_dist = (
            F["reviews"]["sentiment_label"].value_counts(normalize=True)
            .reindex(["positive", "neutral", "negative"], fill_value=0) * 100
        ).round(1)
        fig2 = go.Figure(go.Bar(
            x=sent_dist.values,
            y=sent_dist.index,
            orientation="h",
            marker_color=[SENTIMENT_COLORS[s] for s in sent_dist.index],
            text=[f"{v:.1f}%" for v in sent_dist.values],
            textposition="outside",
        ))
        fig2.update_layout(
            height=200,
            margin=dict(l=10, r=30, t=10, b=10),
            plot_bgcolor="white",
            xaxis=dict(showticklabels=False, range=[0, max(sent_dist.values) * 1.25]),
            yaxis=dict(showgrid=False),
        )
        st.plotly_chart(fig2, use_container_width=True)

        st.markdown("##### Positioning")
        if "positioning" in DATA["vfm"].columns:
            for _, row in DATA["vfm"].iterrows():
                if row["brand"] not in selected_brands:
                    continue
                pos = row["positioning"]
                badge_class = pos.lower().replace("-", "")
                st.markdown(
                    f"<div style='display:flex;justify-content:space-between;align-items:center;"
                    f"padding:6px 0;border-bottom:1px solid #f1f5f9;'>"
                    f"<span style='font-weight:500'>{row['brand']}</span>"
                    f"<span class='badge {badge_class}'>{pos}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

    st.markdown("---")
    st.markdown("### Pricing & discount snapshot")

    col_a, col_b = st.columns(2)
    with col_a:
        fig_price = px.bar(
            overview.sort_values("avg_price", ascending=True),
            x="avg_price",
            y="brand",
            orientation="h",
            color="brand",
            color_discrete_map=BRAND_COLORS,
            text=overview.sort_values("avg_price", ascending=True)["avg_price"].apply(lambda x: f"₹{x:,.0f}"),
            labels={"avg_price": "Average price (₹)", "brand": ""},
            title="Average selling price by brand",
        )
        fig_price.update_traces(textposition="outside")
        fig_price.update_layout(
            showlegend=False,
            plot_bgcolor="white",
            height=350,
            margin=dict(l=10, r=80, t=50, b=10),
        )
        st.plotly_chart(fig_price, use_container_width=True)

    with col_b:
        fig_disc = px.bar(
            overview.sort_values("avg_discount_pct", ascending=True),
            x="avg_discount_pct",
            y="brand",
            orientation="h",
            color="brand",
            color_discrete_map=BRAND_COLORS,
            text=overview.sort_values("avg_discount_pct", ascending=True)["avg_discount_pct"].apply(lambda x: f"{x:.0f}%"),
            labels={"avg_discount_pct": "Average discount (%)", "brand": ""},
            title="Discount dependence by brand",
        )
        fig_disc.update_traces(textposition="outside")
        fig_disc.update_layout(
            showlegend=False,
            plot_bgcolor="white",
            height=350,
            margin=dict(l=10, r=60, t=50, b=10),
        )
        st.plotly_chart(fig_disc, use_container_width=True)
        st.caption("Higher bar = more reliance on MRP-discount tactics to drive sales.")

    st.markdown("### Product price spread")
    fig_box = px.box(
        F["products"].dropna(subset=["price"]),
        x="brand",
        y="price",
        color="brand",
        color_discrete_map=BRAND_COLORS,
        points="all",
        title="Price distribution per brand (each dot = one product)",
        height=400,
    )
    fig_box.update_layout(
        showlegend=False,
        plot_bgcolor="white",
        yaxis_title="Price (₹)",
        xaxis_title="",
    )
    st.plotly_chart(fig_box, use_container_width=True)


# -----------------------------------------------------------------------------
# BRAND COMPARISON TAB
# -----------------------------------------------------------------------------
with tab_compare:
    st.markdown("### Side-by-side brand benchmarking")

    # Build the full comparison table from filtered data
    comp = (
        F["products"]
        .groupby("brand")
        .agg(
            products=("asin", "count"),
            avg_price=("price", "mean"),
            avg_list_price=("list_price", "mean"),
            avg_discount=("discount_pct", "mean"),
            avg_rating=("rating", "mean"),
            total_ratings=("rating_count", "sum"),
        )
        .round(2)
        .reset_index()
    )
    review_agg = (
        F["reviews"]
        .groupby("brand")
        .agg(
            reviews_in_view=("review_id", "count"),
            sentiment=("sentiment_compound", "mean"),
            pct_pos=("sentiment_label", lambda s: (s == "positive").mean() * 100),
            pct_neg=("sentiment_label", lambda s: (s == "negative").mean() * 100),
        )
        .round(2)
        .reset_index()
    )
    comp = comp.merge(review_agg, on="brand", how="outer")
    if "vfm_score" in DATA["vfm"].columns:
        comp = comp.merge(
            DATA["vfm"][["brand", "vfm_score", "positioning"]], on="brand", how="left"
        )

    # Sortable display
    sort_by = st.selectbox(
        "Sort by",
        options=[
            "vfm_score", "sentiment", "avg_price", "avg_discount",
            "avg_rating", "total_ratings",
        ],
        format_func=lambda s: {
            "vfm_score": "Value-for-Money score",
            "sentiment": "Sentiment score",
            "avg_price": "Average price",
            "avg_discount": "Average discount %",
            "avg_rating": "Average star rating",
            "total_ratings": "Total ratings (volume)",
        }[s],
        index=0,
    )
    ascending = st.checkbox("Ascending", value=False)

    comp_display = comp.sort_values(
        sort_by, ascending=ascending, na_position="last"
    ).reset_index(drop=True)

    st.dataframe(
        comp_display.style.format({
            "avg_price": "₹{:,.0f}",
            "avg_list_price": "₹{:,.0f}",
            "avg_discount": "{:.1f}%",
            "avg_rating": "{:.2f}★",
            "total_ratings": "{:,.0f}",
            "sentiment": "{:+.2f}",
            "pct_pos": "{:.1f}%",
            "pct_neg": "{:.1f}%",
            "vfm_score": "{:+.2f}",
        }, na_rep="—"),
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("---")
    st.markdown("### Aspect-level head-to-head")
    st.caption(
        "How brands score on individual aspects (wheels, durability, zipper, etc). "
        "Net score = (positive mentions − negative mentions) / total mentions."
    )

    asp = DATA["aspects"][DATA["aspects"]["brand"].isin(selected_brands)]
    if not asp.empty:
        pivot = asp.pivot_table(
            index="aspect", columns="brand", values="net_score", aggfunc="mean"
        ).round(2)

        fig_heat = go.Figure(data=go.Heatmap(
            z=pivot.values,
            x=pivot.columns,
            y=pivot.index,
            colorscale=[[0, "#ef4444"], [0.5, "#f8fafc"], [1, "#10b981"]],
            zmid=0,
            text=pivot.values,
            texttemplate="%{text:+.2f}",
            textfont={"size": 11},
            colorbar=dict(title="Net score"),
        ))
        fig_heat.update_layout(
            height=420,
            margin=dict(l=10, r=10, t=20, b=10),
            xaxis=dict(side="top"),
        )
        st.plotly_chart(fig_heat, use_container_width=True)

    st.markdown("---")
    st.markdown("### Top pros & cons by brand")

    pros_cons_cols = st.columns(min(3, len(selected_brands)) or 1)
    for i, brand in enumerate(selected_brands):
        col = pros_cons_cols[i % len(pros_cons_cols)]
        with col:
            st.markdown(f"**{brand}**")
            sub = F["reviews"][F["reviews"]["brand"] == brand]
            if sub.empty:
                st.write("No reviews in current filter.")
                continue

            # Aggregate aspect labels for this brand
            from collections import Counter
            pos_counter, neg_counter = Counter(), Counter()
            for asps in sub["aspects"]:
                for a, lbl in asps.items():
                    if lbl == "positive":
                        pos_counter[a] += 1
                    elif lbl == "negative":
                        neg_counter[a] += 1

            st.markdown("👍 **Top praises**")
            if pos_counter:
                for asp_name, count in pos_counter.most_common(3):
                    st.markdown(f"- {asp_name.replace('_', ' ')} ({count} mentions)")
            else:
                st.markdown("_no positive mentions in current filter_")

            st.markdown("👎 **Top complaints**")
            if neg_counter:
                for asp_name, count in neg_counter.most_common(3):
                    st.markdown(f"- {asp_name.replace('_', ' ')} ({count} mentions)")
            else:
                st.markdown("_no negative mentions in current filter_")


# -----------------------------------------------------------------------------
# PRODUCT DRILLDOWN TAB
# -----------------------------------------------------------------------------
with tab_drilldown:
    st.markdown("### Single-product deep dive")

    if F["products"].empty:
        st.info("No products match the current filters.")
    else:
        # Two-step picker — brand then product
        cols = st.columns([1, 3])
        with cols[0]:
            chosen_brand = st.selectbox(
                "Brand",
                options=sorted(F["products"]["brand"].unique()),
                key="dd_brand",
            )
        brand_products = F["products"][F["products"]["brand"] == chosen_brand]
        with cols[1]:
            # Show price + rating in the dropdown so picker is informative
            def _label(asin):
                row = brand_products[brand_products["asin"] == asin].iloc[0]
                tt = row["title"][:80] + ("…" if len(str(row["title"])) > 80 else "")
                price_str = f"₹{row['price']:,.0f}" if pd.notna(row["price"]) else "—"
                rating_str = f"{row['rating']:.1f}★" if pd.notna(row["rating"]) else "—"
                return f"{tt}  ·  {price_str}  ·  {rating_str}"
            chosen_asin = st.selectbox(
                "Product",
                options=brand_products["asin"].tolist(),
                format_func=_label,
                key="dd_asin",
            )

        prod = brand_products[brand_products["asin"] == chosen_asin].iloc[0]

        st.markdown("---")
        info_cols = st.columns([2, 1, 1, 1, 1])
        with info_cols[0]:
            st.markdown(f"**{prod['title']}**")
            st.caption(f"ASIN: {prod['asin']} · Category: {prod.get('category_hint', '—')}")
        with info_cols[1]:
            price = prod["price"]
            st.metric("Selling price", f"₹{price:,.0f}" if pd.notna(price) else "—")
        with info_cols[2]:
            lp = prod.get("list_price")
            st.metric("List price (MRP)", f"₹{lp:,.0f}" if pd.notna(lp) else "—")
        with info_cols[3]:
            d = prod.get("discount_pct")
            st.metric("Discount", f"{d:.0f}%" if pd.notna(d) else "—")
        with info_cols[4]:
            r = prod.get("rating")
            rc = prod.get("rating_count")
            st.metric(
                "Rating",
                f"{r:.1f}★" if pd.notna(r) else "—",
                f"{int(rc):,} ratings" if pd.notna(rc) else None,
            )

        # Reviews for this specific product
        prod_reviews = F["reviews"][F["reviews"]["asin"] == chosen_asin]

        st.markdown(f"#### Review synthesis · {len(prod_reviews)} reviews in current filter")

        if prod_reviews.empty:
            st.info("No reviews for this product within the current filter.")
        else:
            sent_cols = st.columns(4)
            with sent_cols[0]:
                avg_sent = prod_reviews["sentiment_compound"].mean()
                st.metric("Avg sentiment", f"{avg_sent:+.2f}")
            with sent_cols[1]:
                pos = (prod_reviews["sentiment_label"] == "positive").mean() * 100
                st.metric("Positive", f"{pos:.0f}%")
            with sent_cols[2]:
                neg = (prod_reviews["sentiment_label"] == "negative").mean() * 100
                st.metric("Negative", f"{neg:.0f}%")
            with sent_cols[3]:
                verified = prod_reviews["verified"].fillna(False).mean() * 100
                st.metric("Verified buys", f"{verified:.0f}%")

            # Per-aspect breakdown for this product
            from collections import Counter
            aspect_pos, aspect_neg = Counter(), Counter()
            for asps in prod_reviews["aspects"]:
                for a, lbl in asps.items():
                    if lbl == "positive":
                        aspect_pos[a] += 1
                    elif lbl == "negative":
                        aspect_neg[a] += 1

            st.markdown("##### Aspect mentions")
            all_aspects = set(aspect_pos) | set(aspect_neg)
            if all_aspects:
                aspect_df = pd.DataFrame([
                    {"aspect": a, "positive": aspect_pos[a], "negative": -aspect_neg[a]}
                    for a in sorted(all_aspects)
                ])
                fig_asp = go.Figure()
                fig_asp.add_trace(go.Bar(
                    y=aspect_df["aspect"], x=aspect_df["positive"], orientation="h",
                    name="Positive", marker_color=SENTIMENT_COLORS["positive"],
                ))
                fig_asp.add_trace(go.Bar(
                    y=aspect_df["aspect"], x=aspect_df["negative"], orientation="h",
                    name="Negative", marker_color=SENTIMENT_COLORS["negative"],
                ))
                fig_asp.update_layout(
                    barmode="relative",
                    height=300,
                    plot_bgcolor="white",
                    margin=dict(l=10, r=10, t=10, b=10),
                    xaxis=dict(title="Mentions (negative ← 0 → positive)"),
                )
                st.plotly_chart(fig_asp, use_container_width=True)
            else:
                st.caption("No aspects detected in this product's reviews.")

            # Sample reviews
            st.markdown("##### Representative reviews")
            tab_pos, tab_neg = st.tabs(["👍 Most positive", "👎 Most negative"])

            def _render_quote(row, klass):
                title = row.get("title") or ""
                body = row.get("body") or ""
                rating = row.get("rating")
                rating_str = f"{rating:.0f}★" if pd.notna(rating) else "—"
                st.markdown(
                    f"<div class='review-quote {klass}'>"
                    f"<strong>{title}</strong><br/>{body}"
                    f"<div class='quote-meta'>{rating_str} · sentiment {row['sentiment_compound']:+.2f}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

            with tab_pos:
                top_pos = prod_reviews.nlargest(3, "sentiment_compound")
                if top_pos.empty:
                    st.caption("No positive reviews found.")
                for _, r in top_pos.iterrows():
                    _render_quote(r, "positive")

            with tab_neg:
                top_neg = prod_reviews.nsmallest(3, "sentiment_compound")
                if top_neg.empty:
                    st.caption("No negative reviews found.")
                for _, r in top_neg.iterrows():
                    _render_quote(r, "negative")


# -----------------------------------------------------------------------------
# AGENT INSIGHTS TAB
# -----------------------------------------------------------------------------
with tab_insights:
    st.markdown("### 🧠 Agent Insights")
    st.markdown(
        "<p style='color:#64748b;'>"
        "Auto-generated non-obvious conclusions, ranked by how much they deviate from the dataset average. "
        "Each insight is grounded in concrete numbers from the data — no LLM hallucinations."
        "</p>",
        unsafe_allow_html=True,
    )

    insights = DATA["insights"]
    if not insights:
        st.info("No insights generated. Re-run `python -m analysis.pipeline`.")
    else:
        for i, ins in enumerate(insights, 1):
            cat = ins.get("category", "sentiment")
            evidence_str = " · ".join(f"{k}: {v}" for k, v in ins["evidence"].items())
            st.markdown(
                f"<div class='insight-card {cat}'>"
                f"<div class='insight-headline'>#{i} · {ins['headline']}</div>"
                f"<div class='insight-body'>{ins['body']}</div>"
                f"<div class='insight-evidence'>📊 {evidence_str}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    st.markdown("---")
    st.markdown("### 💰 Value-for-Money leaderboard")
    st.caption(
        "VFM = z(sentiment) − z(category-adjusted price). Positive scores mean the brand "
        "delivers more review-text positivity than its price band would predict."
    )

    vfm = DATA["vfm"][DATA["vfm"]["brand"].isin(selected_brands)].sort_values(
        "vfm_score", ascending=False
    )
    if not vfm.empty:
        fig_vfm = px.bar(
            vfm,
            x="vfm_score",
            y="brand",
            orientation="h",
            color="vfm_score",
            color_continuous_scale=[[0, "#ef4444"], [0.5, "#f8fafc"], [1, "#10b981"]],
            color_continuous_midpoint=0,
            text=vfm["vfm_score"].apply(lambda x: f"{x:+.2f}"),
            labels={"vfm_score": "Value-for-Money score", "brand": ""},
            hover_data={"positioning": True, "avg_price": ":,.0f", "sentiment_score": ":+.2f"},
            height=350,
        )
        fig_vfm.update_traces(textposition="outside")
        fig_vfm.update_layout(
            plot_bgcolor="white",
            coloraxis_showscale=False,
            margin=dict(l=10, r=60, t=20, b=10),
            yaxis=dict(categoryorder="total ascending"),
        )
        st.plotly_chart(fig_vfm, use_container_width=True)
