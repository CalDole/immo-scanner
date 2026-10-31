from immo_scanner.crawlers.base import BaseCrawler
from immo_scanner.crawlers.bsk import BskCrawler
from immo_scanner.crawlers.engelvoelkers import EngelVoelkersCrawler
from immo_scanner.crawlers.immoscout24 import ImmoScout24Crawler
from immo_scanner.crawlers.immowelt import ImmoweltCrawler
from immo_scanner.crawlers.kleinanzeigen import KleinanzeigenCrawler
from immo_scanner.crawlers.neubaukompass import NeubaukompassCrawler

CRAWLERS: dict[str, type[BaseCrawler]] = {
    crawler.source: crawler
    for crawler in (
        KleinanzeigenCrawler,
        ImmoweltCrawler,
        NeubaukompassCrawler,
        ImmoScout24Crawler,
        BskCrawler,
        EngelVoelkersCrawler,
    )
}
