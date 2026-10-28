"""Public media cards from the existing bounded, public-address-only fetcher."""

import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlsplit

from .common import ServiceError
from .fetch import PageFetcher, checked_url, decoded, is_public


class Metadata(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "meta":
            key = values.get("property") or values.get("name")
            value = values.get("content")
            if key and value:
                self.meta.setdefault(key.lower(), " ".join(value.split())[:1000])
        self.in_title = self.in_title or tag == "title"

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title = (self.title + data)[:300]


def asset(url: str, base: str) -> str | None:
    try:
        parsed = checked_url(urljoin(base, url))
    except ServiceError:
        return None
    host = parsed.host.lower()
    if host == "localhost" or host.endswith((".localhost", ".local")):
        return None
    import ipaddress
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not is_public(host):
            return None
    return str(parsed)


def youtube_id(url: str) -> str | None:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if host in ("youtu.be", "www.youtu.be"):
        value = parsed.path.strip("/")
    elif host in ("youtube.com", "www.youtube.com", "m.youtube.com", "www.youtube-nocookie.com"):
        value = parse_qs(parsed.query).get("v", [""])[0]
        if parsed.path.startswith(("/embed/", "/shorts/", "/live/")):
            value = parsed.path.split("/")[2]
    else:
        return None
    return value if re.fullmatch(r"[A-Za-z0-9_-]{11}", value) else None


async def preview(url: str, fetcher: PageFetcher) -> dict:
    checked_url(url)
    fetched = await fetcher.fetch(url)
    parsed = urlsplit(fetched.url)
    video = youtube_id(fetched.url)
    path = parsed.path.lower()
    kind = "video" if video else "pdf" if fetched.content_type == "application/pdf" or path.endswith(".pdf") else "image" if fetched.content_type.startswith("image/") or path.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif")) else "link"
    info = Metadata()
    if fetched.content_type == "text/html":
        info.feed(decoded(fetched))
    meta = info.meta
    thumbnail = asset(meta.get("og:image", ""), fetched.url) if meta.get("og:image") else None
    if thumbnail:
        try:
            thumbnail = await fetcher.public_url(thumbnail)
        except ServiceError:
            thumbnail = None
    return {
        "type": kind, "url": fetched.url,
        "title": meta.get("og:title") or " ".join(info.title.split()) or parsed.hostname or url,
        "site": meta.get("og:site_name") or parsed.hostname or "",
        "description": meta.get("og:description") or meta.get("description", ""),
        "thumbnail": fetched.url if kind == "image" else thumbnail,
        "embed": f"https://www.youtube-nocookie.com/embed/{video}" if video else None,
    }
