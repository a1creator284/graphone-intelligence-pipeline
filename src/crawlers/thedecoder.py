"""The Decoder: public AI-news RSS, using the existing article extraction path."""
from src.crawlers.news_base import RSSNewsAdapter


class TheDecoderAdapter(RSSNewsAdapter):
    name = "thedecoder_rss"
    feed_url = "https://the-decoder.com/feed/"
