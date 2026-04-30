"""
Central configuration for the Amazon India luggage scraper.

Edit BRANDS to expand coverage. Each brand maps to one or more Amazon search
terms — Amazon's search occasionally surfaces different listings for slight
phrasing variations, so we use 1-2 terms per brand for better recall.
"""

# Brands to track. Order here is the order they'll appear in dashboard dropdowns.
BRANDS = {
    "Safari": ["Safari luggage", "Safari trolley bag"],
    "Skybags": ["Skybags luggage", "Skybags trolley"],
    "American Tourister": ["American Tourister luggage", "American Tourister trolley"],
    "VIP": ["VIP luggage", "VIP trolley bag"],
    "Aristocrat": ["Aristocrat luggage", "Aristocrat trolley"],
    "Nasher Miles": ["Nasher Miles luggage", "Nasher Miles trolley"],
}

# Per-brand collection targets. Assignment minimum is 10 products and 50 reviews
# per brand — we aim a bit higher for better statistics.
TARGET_PRODUCTS_PER_BRAND = 12
TARGET_REVIEWS_PER_PRODUCT = 8  # 12 products * 8 reviews = ~96 reviews per brand
MAX_REVIEW_PAGES = 3            # Hard cap so the scraper doesn't run forever

# Amazon India base. Country domain matters — .in returns INR pricing,
# India-specific availability, and Hindi/English review mix.
AMAZON_BASE = "https://www.amazon.in"

# Anti-detection. Amazon fingerprints aggressively. These help but won't
# defeat a determined block — if you get CAPTCHA'd, slow down further or
# switch IPs. Do NOT remove the delays.
HEADLESS = False                # Headless is more detectable; run headed when possible
MIN_DELAY_SECONDS = 2.5         # Lower bound on inter-request delay
MAX_DELAY_SECONDS = 6.0         # Upper bound — randomized to look human
PAGE_TIMEOUT_MS = 45_000        # Slow connections + India routing = generous timeout
RETRY_ATTEMPTS = 3              # Per page, with exponential backoff

# Realistic desktop user agents. We rotate per session to reduce fingerprint
# stability. Keep these reasonably current — old Chrome versions get flagged.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]

# Output paths
RAW_DATA_DIR = "data/raw"
PROCESSED_DATA_DIR = "data/processed"

# Filenames
PRODUCTS_FILE = "products.json"
REVIEWS_FILE = "reviews.json"
PRODUCTS_CLEAN = "products.csv"
REVIEWS_CLEAN = "reviews.csv"
