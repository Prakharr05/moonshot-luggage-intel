"""
Amazon India product + review scraper using Playwright.

Design notes:
- Amazon rotates DOM selectors and HTML structure constantly. We try multiple
  selectors per field and fall back gracefully. Missing fields => null, not crash.
- We persist after EVERY product so a mid-run block doesn't lose all progress.
- Detection of CAPTCHA / robot-check pages triggers a longer cooldown.
- Run this LOCALLY, not in a sandbox. Amazon blocks datacenter IPs aggressively.

Usage:
    python -m scraper.amazon_scraper

Outputs:
    data/raw/products.json   — list of product dicts
    data/raw/reviews.json    — list of review dicts (linked to products by asin)
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

from playwright.async_api import (
    async_playwright,
    Page,
    Browser,
    BrowserContext,
    TimeoutError as PlaywrightTimeoutError,
)

from scraper.config import (
    BRANDS,
    AMAZON_BASE,
    HEADLESS,
    MIN_DELAY_SECONDS,
    MAX_DELAY_SECONDS,
    PAGE_TIMEOUT_MS,
    RETRY_ATTEMPTS,
    USER_AGENTS,
    TARGET_PRODUCTS_PER_BRAND,
    TARGET_REVIEWS_PER_PRODUCT,
    MAX_REVIEW_PAGES,
    RAW_DATA_DIR,
    PRODUCTS_FILE,
    REVIEWS_FILE,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("amazon-scraper")


# ---------- Data structures -------------------------------------------------

@dataclass
class Product:
    asin: str
    brand: str
    title: str
    url: str
    price: Optional[float] = None        # Selling price (INR)
    list_price: Optional[float] = None   # MRP / "M.R.P." crossed-out price
    discount_pct: Optional[float] = None
    rating: Optional[float] = None       # Avg star rating, 0-5
    rating_count: Optional[int] = None   # Number of ratings (often >> review count)
    review_count: Optional[int] = None   # Number of textual reviews
    image_url: Optional[str] = None
    bullet_features: list[str] = field(default_factory=list)
    category_hint: Optional[str] = None  # Cabin / Check-in / Set, parsed from title


@dataclass
class Review:
    asin: str
    brand: str
    review_id: str
    rating: Optional[float]
    title: str
    body: str
    author: Optional[str] = None
    date: Optional[str] = None
    verified: bool = False
    helpful_count: Optional[int] = None


# ---------- Utilities -------------------------------------------------------

async def human_delay():
    """Random pause between requests so we don't pattern-match as a bot."""
    await asyncio.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))


def parse_inr(text: str) -> Optional[float]:
    """Extract a numeric price from INR text like '₹4,599' or '4,599.00'."""
    if not text:
        return None
    cleaned = re.sub(r"[^\d.]", "", text.replace(",", ""))
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def parse_int(text: str) -> Optional[int]:
    """Extract an integer from text like '1,234 ratings' or '12.5K answered'."""
    if not text:
        return None
    text = text.strip()
    # Handle '12.5K' / '1.2M' style
    m = re.search(r"([\d,.]+)\s*([KMkm]?)", text)
    if not m:
        return None
    raw, suffix = m.group(1), m.group(2).upper()
    try:
        n = float(raw.replace(",", ""))
    except ValueError:
        return None
    if suffix == "K":
        n *= 1_000
    elif suffix == "M":
        n *= 1_000_000
    return int(n)


def guess_category(title: str) -> Optional[str]:
    """Bucket a product title into Cabin / Medium / Large / Set based on keywords.

    This is a pragmatic heuristic — Amazon listings rarely have a clean
    category field, but titles almost always state size class.
    """
    if not title:
        return None
    t = title.lower()
    if "set of" in t or "combo" in t or "pack of" in t:
        return "Set"
    if "cabin" in t or "carry-on" in t or "carry on" in t or "55 cm" in t or "55cm" in t or '20"' in t or "20 inch" in t:
        return "Cabin"
    if "large" in t or "75 cm" in t or "75cm" in t or '28"' in t or "28 inch" in t or "check-in" in t:
        return "Large"
    if "medium" in t or "65 cm" in t or "65cm" in t or '24"' in t or "24 inch" in t:
        return "Medium"
    return None


async def is_blocked(page: Page) -> bool:
    """Detect Amazon's bot-check / CAPTCHA pages."""
    try:
        content = (await page.content()).lower()
    except Exception:
        return False
    blockers = [
        "enter the characters you see below",
        "type the characters you see in this image",
        "to discuss automated access to amazon data",
        "robot check",
        "/errors/validatecaptcha",
    ]
    return any(b in content for b in blockers)


async def safe_text(page: Page, selectors: list[str]) -> Optional[str]:
    """Try multiple selectors; return text content of the first that matches."""
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                t = (await el.inner_text()).strip()
                if t:
                    return t
        except Exception:
            continue
    return None


async def safe_attr(page: Page, selectors: list[str], attr: str) -> Optional[str]:
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                v = await el.get_attribute(attr)
                if v:
                    return v.strip()
        except Exception:
            continue
    return None


# ---------- Scraping logic --------------------------------------------------

async def make_context(browser: Browser) -> BrowserContext:
    """Build a context with realistic fingerprint."""
    ua = random.choice(USER_AGENTS)
    context = await browser.new_context(
        user_agent=ua,
        viewport={"width": 1366, "height": 768},
        locale="en-IN",
        timezone_id="Asia/Kolkata",
        # Setting India geo helps with delivery-availability rendering
        geolocation={"latitude": 28.6139, "longitude": 77.2090},
        permissions=["geolocation"],
    )
    # Strip the obvious 'navigator.webdriver = true' tell
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    return context


async def search_products(page: Page, search_term: str, brand: str) -> list[dict]:
    """Run a search and return [{asin, title, url, image_url}] from result cards."""
    url = f"{AMAZON_BASE}/s?k={search_term.replace(' ', '+')}"
    log.info("  search: %s", search_term)

    for attempt in range(RETRY_ATTEMPTS):
        try:
            await page.goto(url, timeout=PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
            if await is_blocked(page):
                log.warning("  blocked on search; cooling down (%ds)", 30 * (attempt + 1))
                await asyncio.sleep(30 * (attempt + 1))
                continue
            # Wait for at least one result tile
            await page.wait_for_selector(
                'div[data-component-type="s-search-result"]',
                timeout=15_000,
            )
            break
        except PlaywrightTimeoutError:
            log.warning("  search timeout (attempt %d)", attempt + 1)
            await human_delay()
    else:
        log.error("  search failed after %d attempts: %s", RETRY_ATTEMPTS, search_term)
        return []

    cards = await page.query_selector_all('div[data-component-type="s-search-result"]')
    out = []
    for card in cards:
        try:
            asin = await card.get_attribute("data-asin")
            if not asin:
                continue
            # Title + URL — Amazon has used both 'h2 a' and 'h2 a.a-link-normal'
            link_el = await card.query_selector("h2 a")
            if not link_el:
                continue
            href = await link_el.get_attribute("href")
            title = (await link_el.inner_text()).strip()
            if not href or not title:
                continue
            # Skip sponsored placements that don't represent the brand fairly
            sponsored = await card.query_selector('[aria-label="Sponsored"], span:has-text("Sponsored")')

            img_el = await card.query_selector("img.s-image")
            image_url = await img_el.get_attribute("src") if img_el else None

            out.append({
                "asin": asin,
                "brand": brand,
                "title": title,
                "url": urljoin(AMAZON_BASE, href.split("?")[0]),
                "image_url": image_url,
                "sponsored": bool(sponsored),
            })
        except Exception as e:
            log.debug("    card parse skipped: %s", e)
            continue
    return out


async def scrape_product_detail(page: Page, prod: dict) -> Optional[Product]:
    """Visit a product page and extract pricing + ratings + features."""
    for attempt in range(RETRY_ATTEMPTS):
        try:
            await page.goto(prod["url"], timeout=PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
            if await is_blocked(page):
                log.warning("    blocked on PDP; cooling down")
                await asyncio.sleep(30 * (attempt + 1))
                continue
            await page.wait_for_selector("#productTitle, #title", timeout=15_000)
            break
        except PlaywrightTimeoutError:
            log.warning("    PDP timeout (attempt %d) for %s", attempt + 1, prod["asin"])
    else:
        return None

    title = await safe_text(page, ["#productTitle", "#title"]) or prod["title"]

    # Selling price — Amazon has used many variants over the years
    price_text = await safe_text(page, [
        "span.a-price.aok-align-center span.a-offscreen",  # most common 2024-25
        "span.priceToPay span.a-offscreen",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        "span.a-price span.a-offscreen",
    ])
    price = parse_inr(price_text) if price_text else None

    # MRP / list price
    list_price_text = await safe_text(page, [
        "span.a-price.a-text-price span.a-offscreen",
        "span.basisPrice span.a-offscreen",
        "#listPrice",
    ])
    list_price = parse_inr(list_price_text) if list_price_text else None

    # Discount % — sometimes shown directly, sometimes computed
    discount_text = await safe_text(page, [
        "span.savingsPercentage",
        "span.a-color-price.savingPriceOverride",
    ])
    discount_pct = None
    if discount_text:
        m = re.search(r"(\d+)\s*%", discount_text)
        if m:
            discount_pct = float(m.group(1))
    if discount_pct is None and price and list_price and list_price > price:
        discount_pct = round((list_price - price) / list_price * 100, 1)

    # Rating
    rating_text = await safe_text(page, [
        "span.a-icon-alt",
        "i.a-icon-star span.a-icon-alt",
        "#acrPopover",
    ])
    rating = None
    if rating_text:
        m = re.search(r"([\d.]+)\s*out of\s*5", rating_text)
        if m:
            try:
                rating = float(m.group(1))
            except ValueError:
                pass

    # Rating count
    rating_count_text = await safe_text(page, [
        "#acrCustomerReviewText",
        "span[data-hook='total-review-count']",
    ])
    rating_count = parse_int(rating_count_text) if rating_count_text else None

    # Bullet features (the "About this item" list)
    bullets = []
    try:
        bullet_els = await page.query_selector_all(
            "#feature-bullets ul li span.a-list-item, #featurebullets_feature_div li span"
        )
        for b in bullet_els[:8]:
            t = (await b.inner_text()).strip()
            if t and len(t) > 5:
                bullets.append(t)
    except Exception:
        pass

    return Product(
        asin=prod["asin"],
        brand=prod["brand"],
        title=title,
        url=prod["url"],
        price=price,
        list_price=list_price,
        discount_pct=discount_pct,
        rating=rating,
        rating_count=rating_count,
        review_count=None,  # filled in by review scrape
        image_url=prod.get("image_url"),
        bullet_features=bullets,
        category_hint=guess_category(title),
    )


async def scrape_reviews(page: Page, asin: str, brand: str, limit: int) -> list[Review]:
    """Paginate through the all-reviews page and collect up to `limit` reviews."""
    reviews: list[Review] = []
    for page_num in range(1, MAX_REVIEW_PAGES + 1):
        if len(reviews) >= limit:
            break
        url = f"{AMAZON_BASE}/product-reviews/{asin}/?pageNumber={page_num}&sortBy=recent"
        try:
            await page.goto(url, timeout=PAGE_TIMEOUT_MS, wait_until="domcontentloaded")
        except PlaywrightTimeoutError:
            log.debug("    review page timeout asin=%s p=%d", asin, page_num)
            continue

        if await is_blocked(page):
            log.warning("    blocked on reviews; cooling down")
            await asyncio.sleep(30)
            continue

        # Two layout flavours — old and new review HTML
        cards = await page.query_selector_all(
            "div[data-hook='review'], li[data-hook='review']"
        )
        if not cards:
            break

        for c in cards:
            if len(reviews) >= limit:
                break
            try:
                rid = await c.get_attribute("id") or ""
                rating_el = await c.query_selector("i[data-hook='review-star-rating'] span, i[data-hook='cmps-review-star-rating'] span")
                rating_text = (await rating_el.inner_text()).strip() if rating_el else ""
                m = re.search(r"([\d.]+)\s*out of", rating_text)
                rev_rating = float(m.group(1)) if m else None

                title_el = await c.query_selector("a[data-hook='review-title'] span:not([class]), span[data-hook='review-title'] span")
                title = (await title_el.inner_text()).strip() if title_el else ""

                body_el = await c.query_selector("span[data-hook='review-body'] span")
                body = (await body_el.inner_text()).strip() if body_el else ""

                if not body and not title:
                    continue

                author_el = await c.query_selector("span.a-profile-name")
                author = (await author_el.inner_text()).strip() if author_el else None

                date_el = await c.query_selector("span[data-hook='review-date']")
                date = (await date_el.inner_text()).strip() if date_el else None

                verified_el = await c.query_selector("span[data-hook='avp-badge']")
                verified = verified_el is not None

                helpful_el = await c.query_selector("span[data-hook='helpful-vote-statement']")
                helpful_text = (await helpful_el.inner_text()).strip() if helpful_el else ""
                helpful_count = parse_int(helpful_text) if helpful_text else 0

                reviews.append(Review(
                    asin=asin,
                    brand=brand,
                    review_id=rid,
                    rating=rev_rating,
                    title=title,
                    body=body,
                    author=author,
                    date=date,
                    verified=verified,
                    helpful_count=helpful_count,
                ))
            except Exception as e:
                log.debug("      review parse skipped: %s", e)
                continue

        await human_delay()
    return reviews


# ---------- Persistence -----------------------------------------------------

def save_jsonl(items: list[dict], path: Path):
    """Atomic JSON dump — write to .tmp then rename, so partial writes
    can't corrupt prior state if the process dies."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


# ---------- Orchestration ---------------------------------------------------

async def run():
    raw_dir = Path(RAW_DATA_DIR)
    products_path = raw_dir / PRODUCTS_FILE
    reviews_path = raw_dir / REVIEWS_FILE

    all_products: list[dict] = []
    all_reviews: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=HEADLESS)
        context = await make_context(browser)
        page = await context.new_page()

        for brand, search_terms in BRANDS.items():
            log.info("=" * 60)
            log.info("BRAND: %s", brand)
            log.info("=" * 60)

            # Step 1: collect candidate products from search
            seen_asins: set[str] = set()
            candidates: list[dict] = []
            for term in search_terms:
                cards = await search_products(page, term, brand)
                for c in cards:
                    if c["asin"] in seen_asins:
                        continue
                    # Filter: title must mention the brand (search is noisy)
                    if brand.lower().split()[0] not in c["title"].lower():
                        continue
                    seen_asins.add(c["asin"])
                    candidates.append(c)
                await human_delay()
                if len(candidates) >= TARGET_PRODUCTS_PER_BRAND:
                    break

            # Prefer non-sponsored when we have to choose
            candidates.sort(key=lambda x: x.get("sponsored", False))
            candidates = candidates[:TARGET_PRODUCTS_PER_BRAND]
            log.info("  collected %d candidate products", len(candidates))

            # Step 2: visit each PDP for price/rating + scrape its reviews
            for i, prod in enumerate(candidates, 1):
                log.info("  [%d/%d] %s", i, len(candidates), prod["title"][:70])
                detail = await scrape_product_detail(page, prod)
                if not detail:
                    log.warning("    skipped (no detail)")
                    continue
                await human_delay()

                revs = await scrape_reviews(
                    page, detail.asin, brand, TARGET_REVIEWS_PER_PRODUCT
                )
                detail.review_count = len(revs)
                log.info("    %d reviews | ₹%s | %s★",
                         len(revs), detail.price, detail.rating)

                all_products.append(asdict(detail))
                all_reviews.extend(asdict(r) for r in revs)

                # Persist after every product so a mid-run failure isn't fatal
                save_jsonl(all_products, products_path)
                save_jsonl(all_reviews, reviews_path)

                await human_delay()

        await context.close()
        await browser.close()

    log.info("=" * 60)
    log.info("DONE: %d products, %d reviews", len(all_products), len(all_reviews))
    log.info("  -> %s", products_path)
    log.info("  -> %s", reviews_path)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("interrupted — partial data saved")
        sys.exit(0)
