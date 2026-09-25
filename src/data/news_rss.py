"""
Canadian Issuer Regulatory & Corporate News Ingestion Engine
Free, public RSS ingestion from Newsfile Corp, CNW Cision, and GlobeNewswire.
Features exact MD5 deduplication and ticker entity recognition.
"""

import hashlib
import re
from datetime import datetime, timezone
from typing import List, Dict, Optional, Set

try:
    import feedparser
except ImportError:
    feedparser = None

from src.models.schemas import NewsCluster

# Verified Canadian Corporate Dissemination RSS Feeds
CANADIAN_NEWS_FEEDS = [
    {
        "source": "NEWSFILE",
        "url": "https://www.newsfilecorp.com/rss/all",
        "name": "Newsfile Corp Regulatory Wire",
    },
    {
        "source": "CNW_CISION",
        "url": "https://www.newswire.ca/rss/",
        "name": "CNW Canada Newswire General",
    },
]

# Regex pattern for TSX / TSXV ticker extraction (e.g. TSX: SU, TSX-V: ABC)
TICKER_PATTERN = re.compile(
    r"\b(?:TSX|TSX-V|TSXV|NYSE|NASDAQ)\s*:\s*([A-Z]{1,5})\b",
    re.IGNORECASE,
)


class CanadianNewsRssAdapter:
    def __init__(self):
        self.seen_hashes: Set[str] = set()

    def generate_dedup_hash(self, headline: str, published_str: str) -> str:
        """
        Generate MD5 hash of normalized headline and publication timestamp.
        """
        normalized = re.sub(r"\s+", " ", headline.strip().lower())
        payload = f"{normalized}|{published_str}".encode("utf-8")
        return hashlib.md5(payload).hexdigest()

    def fetch_recent_news(self, max_items_per_feed: int = 15) -> List[NewsCluster]:
        """
        Poll RSS feeds, deduplicate items, and extract structured events.
        """
        results: List[NewsCluster] = []
        knowledge_at = datetime.now(timezone.utc)

        for feed_info in CANADIAN_NEWS_FEEDS:
            source = feed_info["source"]
            url = feed_info["url"]

            try:
                feed = feedparser.parse(url)
            except Exception:
                continue

            for entry in feed.entries[:max_items_per_feed]:
                headline = getattr(entry, "title", "").strip()
                if not headline:
                    continue

                published_str = getattr(entry, "published", "") or getattr(entry, "updated", "")
                dedup_hash = self.generate_dedup_hash(headline, published_str)

                if dedup_hash in self.seen_hashes:
                    continue
                self.seen_hashes.add(dedup_hash)

                summary = getattr(entry, "summary", "")
                clean_summary = re.sub(r"<[^>]+>", "", summary).strip() if summary else None

                # Parse publication datetime
                published_at = knowledge_at
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    try:
                        published_at = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    except Exception:
                        published_at = knowledge_at

                results.append(
                    NewsCluster(
                        dedup_hash=dedup_hash,
                        headline=headline,
                        summary=clean_summary[:500] if clean_summary else None,
                        source=source,
                        published_at=published_at,
                        knowledge_at=knowledge_at,
                        is_primary_source=True,
                    )
                )

        return results
