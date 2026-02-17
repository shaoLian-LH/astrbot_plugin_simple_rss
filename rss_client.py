import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import aiohttp
from astrbot.api import logger

from .rss import RSSItem


class RSSClient:
    def __init__(self, user_agent: str = "astrbot-plugin-simple-rss/1.1.0"):
        self.user_agent = user_agent

    def normalize_url(self, value: str) -> str:
        raw = (value or "").strip()
        if not raw:
            return ""

        if not re.match(r"^https?://", raw, flags=re.IGNORECASE):
            raw = "https://" + raw

        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""

        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path or ''}"
        if parsed.query:
            normalized += f"?{parsed.query}"
        return normalized

    async def fetch(self, url: str, limit: int) -> Tuple[str, str, List[RSSItem]]:
        body = await self._fetch_url_bytes(url)
        if body is None:
            raise RuntimeError("请求失败")

        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            raise RuntimeError(f"XML 解析失败: {exc}")

        title, description = self._extract_feed_info(root)
        items = self._extract_items(root, base_url=url, limit=limit)
        return title, description, items

    async def _fetch_url_bytes(self, url: str) -> Optional[bytes]:
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        headers = {"User-Agent": self.user_agent}
        connector = aiohttp.TCPConnector(ssl=False)
        try:
            async with aiohttp.ClientSession(
                trust_env=True,
                timeout=timeout,
                headers=headers,
                connector=connector,
            ) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"HTTP {resp.status}")
                    return await resp.read()
        except Exception as exc:
            logger.warning(f"请求 RSS 失败: {url} - {exc}")
            return None

    def _extract_feed_info(self, root: ET.Element) -> Tuple[str, str]:
        channel = self._first_node(root, {"channel"})
        if channel is not None:
            title = self._direct_child_text(channel, {"title"})
            description = self._direct_child_text(channel, {"description"})
            return title, description

        title = self._direct_child_text(root, {"title"})
        description = self._direct_child_text(root, {"subtitle", "description"})
        return title, description

    def _extract_items(self, root: ET.Element, base_url: str, limit: int) -> List[RSSItem]:
        nodes: List[ET.Element] = []
        for elem in root.iter():
            name = self._local_name(elem.tag)
            if name in {"item", "entry"}:
                nodes.append(elem)

        items: List[RSSItem] = []
        for node in nodes:
            parsed = self._parse_item_node(node, base_url=base_url)
            if parsed is None:
                continue
            items.append(parsed)
            if limit > 0 and len(items) >= limit:
                break

        return items

    def _parse_item_node(self, node: ET.Element, base_url: str) -> Optional[RSSItem]:
        title = self._direct_child_text(node, {"title"}) or "无标题"
        link = self._extract_item_link(node)
        if link and not re.match(r"^https?://", link, flags=re.IGNORECASE):
            link = urljoin(base_url, link)

        summary = self._direct_child_text(node, {"description", "summary", "content"})
        summary = self._strip_html(summary)

        published = self._direct_child_text(node, {"pubDate", "published", "updated", "date"})
        published_ts = self._parse_datetime(published)

        return RSSItem(
            title=title.strip(),
            link=(link or "").strip(),
            summary=summary,
            published=(published or "").strip(),
            published_ts=published_ts,
        )

    def _extract_item_link(self, node: ET.Element) -> str:
        fallback_href = ""
        for child in node:
            if self._local_name(child.tag) != "link":
                continue

            href = (child.attrib.get("href") or "").strip()
            rel = (child.attrib.get("rel") or "").strip()

            if href and (not rel or rel == "alternate"):
                return href
            if href and not fallback_href:
                fallback_href = href

            text = (child.text or "").strip()
            if text:
                return text

        return fallback_href

    def _direct_child_text(self, node: ET.Element, accepted_names: Set[str]) -> str:
        for child in node:
            if self._local_name(child.tag) in accepted_names:
                text = "".join(child.itertext()).strip()
                if text:
                    return text
        return ""

    def _first_node(self, root: ET.Element, names: Set[str]) -> Optional[ET.Element]:
        for elem in root.iter():
            if self._local_name(elem.tag) in names:
                return elem
        return None

    def _local_name(self, tag: Any) -> str:
        if not isinstance(tag, str):
            return ""
        if "}" in tag:
            return tag.rsplit("}", 1)[-1]
        return tag

    def _strip_html(self, html: str) -> str:
        text = html or ""
        text = re.sub(r"<br\\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = unescape(text)
        text = re.sub(r"\r", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        return text.strip()

    def _parse_datetime(self, raw: str) -> int:
        value = (raw or "").strip()
        if not value:
            return 0

        try:
            dt = parsedate_to_datetime(value)
            if dt is not None:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp())
        except Exception:
            pass

        try:
            iso = value.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except Exception:
            return 0
