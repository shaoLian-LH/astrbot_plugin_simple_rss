import os
import sqlite3
from typing import Any, Dict, List, Tuple

FALLBACK_CRON = "*/30 * * * *"
DEFAULT_DB_PATH = "data/plugins/astrbot_plugin_simple_rss/srss.db"
DEFAULT_DATA_PATH = DEFAULT_DB_PATH


class DataHandler:
    def __init__(self, path: str = DEFAULT_DB_PATH):
        self.path = path
        self._ensure_parent_dir()
        self._init_db()
        self.data = self._load_data()

    def _ensure_parent_dir(self) -> None:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS feeds (
                    url TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS subscriptions (
                    url TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    cron_expr TEXT NOT NULL DEFAULT '',
                    last_update INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (url, channel),
                    FOREIGN KEY (url) REFERENCES feeds(url) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS subscription_recent_ids (
                    url TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    item_id TEXT NOT NULL,
                    PRIMARY KEY (url, channel, position),
                    FOREIGN KEY (url, channel) REFERENCES subscriptions(url, channel) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_subscriptions_channel
                ON subscriptions(channel);

                CREATE INDEX IF NOT EXISTS idx_recent_ids_subscription
                ON subscription_recent_ids(url, channel);
                """
            )

    def _normalize_recent_ids(self, raw: Any) -> List[str]:
        if not isinstance(raw, list):
            return []
        unique: List[str] = []
        for item in raw:
            value = str(item).strip()
            if value and value not in unique:
                unique.append(value)
        return unique

    def _safe_int(self, value: Any) -> int:
        try:
            return int(value)
        except Exception:
            return 0

    def _normalize_feed_entry(self, feed: Any) -> Dict[str, Any]:
        if not isinstance(feed, dict):
            feed = {}

        subscribers_raw = feed.get("subscribers", {})
        subscribers: Dict[str, Dict[str, Any]] = {}
        if isinstance(subscribers_raw, dict):
            for channel, sub in subscribers_raw.items():
                if not isinstance(sub, dict):
                    continue
                subscribers[str(channel)] = {
                    "cron_expr": str(sub.get("cron_expr") or FALLBACK_CRON),
                    "last_update": self._safe_int(sub.get("last_update")),
                    "recent_ids": self._normalize_recent_ids(sub.get("recent_ids")),
                }

        return {
            "title": str(feed.get("title") or ""),
            "description": str(feed.get("description") or ""),
            "subscribers": subscribers,
        }

    def _normalize_data(self, loaded: Any) -> Dict[str, Any]:
        normalized: Dict[str, Any] = {"feeds": {}}
        if not isinstance(loaded, dict):
            return normalized

        feeds = loaded.get("feeds", {})
        if not isinstance(feeds, dict):
            return normalized

        for url, feed in feeds.items():
            url_key = str(url).strip()
            if not url_key:
                continue
            normalized["feeds"][url_key] = self._normalize_feed_entry(feed)

        return normalized

    def _load_data(self) -> Dict[str, Any]:
        feeds: Dict[str, Dict[str, Any]] = {}
        sub_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
        with self._connect() as conn:
            for url, title, description in conn.execute(
                "SELECT url, title, description FROM feeds ORDER BY rowid"
            ):
                feeds[url] = {
                    "title": str(title or ""),
                    "description": str(description or ""),
                    "subscribers": {},
                }

            for url, channel, cron_expr, last_update in conn.execute(
                """
                SELECT url, channel, cron_expr, last_update
                FROM subscriptions
                ORDER BY rowid
                """
            ):
                feed_entry = feeds.setdefault(
                    url,
                    {"title": "", "description": "", "subscribers": {}},
                )
                feed_entry["subscribers"][channel] = {
                    "cron_expr": str(cron_expr or FALLBACK_CRON),
                    "last_update": self._safe_int(last_update),
                    "recent_ids": [],
                }
                sub_map[(url, channel)] = feed_entry["subscribers"][channel]

            for url, channel, _, item_id in conn.execute(
                """
                SELECT url, channel, position, item_id
                FROM subscription_recent_ids
                ORDER BY url, channel, position
                """
            ):
                sub = sub_map.get((url, channel))
                if sub is not None:
                    sub["recent_ids"].append(item_id)

        return {"feeds": feeds}

    def _persist_data(self, data: Dict[str, Any]) -> None:
        feeds = data.get("feeds", {})
        if not isinstance(feeds, dict):
            feeds = {}

        feed_rows: List[Tuple[str, str, str]] = []
        sub_rows: List[Tuple[str, str, str, int]] = []
        recent_rows: List[Tuple[str, str, int, str]] = []

        for url, feed in feeds.items():
            if not isinstance(feed, dict):
                continue

            url_key = str(url).strip()
            if not url_key:
                continue

            title = str(feed.get("title") or "")
            description = str(feed.get("description") or "")
            feed_rows.append((url_key, title, description))

            subscribers = feed.get("subscribers", {})
            if not isinstance(subscribers, dict):
                continue

            for channel, sub in subscribers.items():
                if not isinstance(sub, dict):
                    continue
                channel_key = str(channel).strip()
                if not channel_key:
                    continue
                sub_rows.append(
                    (
                        url_key,
                        channel_key,
                        str(sub.get("cron_expr") or FALLBACK_CRON),
                        self._safe_int(sub.get("last_update")),
                    )
                )
                recent_ids = self._normalize_recent_ids(sub.get("recent_ids"))
                for position, item_id in enumerate(recent_ids):
                    recent_rows.append((url_key, channel_key, position, item_id))

        with self._connect() as conn:
            conn.execute("DELETE FROM subscription_recent_ids")
            conn.execute("DELETE FROM subscriptions")
            conn.execute("DELETE FROM feeds")
            if feed_rows:
                conn.executemany(
                    "INSERT INTO feeds (url, title, description) VALUES (?, ?, ?)",
                    feed_rows,
                )
            if sub_rows:
                conn.executemany(
                    """
                    INSERT INTO subscriptions (url, channel, cron_expr, last_update)
                    VALUES (?, ?, ?, ?)
                    """,
                    sub_rows,
                )
            if recent_rows:
                conn.executemany(
                    """
                    INSERT INTO subscription_recent_ids (url, channel, position, item_id)
                    VALUES (?, ?, ?, ?)
                    """,
                    recent_rows,
                )

    def save_data(self) -> None:
        normalized = self._normalize_data(self.data)
        self._persist_data(normalized)
        self.data = normalized

    def list_channel_subscriptions(self, channel: str) -> List[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
        results: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []
        feeds = self.data.get("feeds", {})
        if not isinstance(feeds, dict):
            return results

        for url, feed in feeds.items():
            if not isinstance(feed, dict):
                continue
            subscribers = feed.get("subscribers", {})
            if not isinstance(subscribers, dict):
                continue
            if channel in subscribers and isinstance(subscribers[channel], dict):
                results.append((url, feed, subscribers[channel]))
        return results
