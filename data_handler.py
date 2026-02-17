import json
import os
from typing import Any, Dict, List, Tuple

FALLBACK_CRON = "*/30 * * * *"
DEFAULT_DATA_PATH = "data/astrbot_plugin_simple_rss_data.json"


class DataHandler:
    def __init__(self, path: str = DEFAULT_DATA_PATH):
        self.path = path
        self.data = self._load_data()

    def _load_data(self) -> Dict[str, Any]:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if not os.path.exists(self.path):
            data = {"feeds": {}}
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return data

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except Exception:
            loaded = {"feeds": {}}

        return self._normalize_data(loaded)

    def _normalize_data(self, loaded: Any) -> Dict[str, Any]:
        if isinstance(loaded, dict) and isinstance(loaded.get("feeds"), dict):
            return loaded

        migrated: Dict[str, Any] = {"feeds": {}}
        if not isinstance(loaded, dict):
            return migrated

        for url, value in loaded.items():
            if not isinstance(value, dict) or "subscribers" not in value:
                continue
            info = value.get("info") if isinstance(value.get("info"), dict) else {}
            subscribers = value.get("subscribers") if isinstance(value.get("subscribers"), dict) else {}
            feed_entry = {
                "title": str(info.get("title") or ""),
                "description": str(info.get("description") or ""),
                "subscribers": {},
            }
            for channel, sub in subscribers.items():
                if not isinstance(sub, dict):
                    continue
                latest_link = str(sub.get("latest_link") or "").strip()
                recent_ids = [latest_link] if latest_link else []
                feed_entry["subscribers"][channel] = {
                    "cron_expr": str(sub.get("cron_expr") or FALLBACK_CRON),
                    "last_update": int(sub.get("last_update") or 0),
                    "recent_ids": recent_ids,
                }
            migrated["feeds"][url] = feed_entry

        return migrated

    def save_data(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

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
