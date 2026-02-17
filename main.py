import hashlib
import shlex
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register

from .cron_utils import parse_cron_expr, validate_cron_expr
from .data_handler import FALLBACK_CRON, DataHandler
from .rss import RSSItem
from .rss_client import RSSClient


PLUGIN_VERSION = "0.0.4"


@register("astrbot_plugin_simple_rss", "slfk", "最简 RSS 订阅插件", PLUGIN_VERSION)
class SimpleRSSPlugin(Star):
    def __init__(self, context: Context, config: Optional[AstrBotConfig] = None):
        super().__init__(context)
        self.context = context
        self.config = config

        self.default_cron_expr = self._read_default_cron_expr()
        self.init_fetch_count = self._read_int_config("init_fetch_count", 20, min_value=1)
        self.poll_fetch_count = self._read_int_config(
            "poll_fetch_count", self.init_fetch_count, min_value=1
        )
        legacy_desc_max = self._to_int(
            self._config_get("description_max_length", 150), 150, min_value=1
        )
        self.desc_max_length = self._to_int(
            self._config_get("desc_max_length", legacy_desc_max),
            legacy_desc_max,
            min_value=1,
        )
        self.display_tz = self._read_display_timezone()

        self.data_handler = DataHandler()
        self.rss_client = RSSClient(desc_max_length=self.desc_max_length)
        self.scheduler = AsyncIOScheduler()

    async def initialize(self):
        if not self.scheduler.running:
            self.scheduler.start()
        self._refresh_scheduler()
        logger.info(
            "simple_rss loaded: version=%s get_format=v2 desc_max_length=%s timezone=%s",
            PLUGIN_VERSION,
            self.desc_max_length,
            self.display_tz,
        )

    async def terminate(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def _config_get(self, key: str, default: Any) -> Any:
        if self.config is None:
            return default
        try:
            value = self.config.get(key)
        except Exception:
            return default
        return default if value is None else value

    def _read_default_cron_expr(self) -> str:
        cron = str(
            self._config_get(
                "default_cron_exp",
                self._config_get("default_corn_exp", FALLBACK_CRON),
            )
        ).strip()
        if not cron:
            cron = FALLBACK_CRON
        ok, _ = validate_cron_expr(cron)
        if not ok:
            cron = FALLBACK_CRON
        return cron

    def _read_int_config(self, key: str, default: int, min_value: int = 0) -> int:
        return self._to_int(self._config_get(key, default), default, min_value)

    def _to_int(self, raw: Any, default: int, min_value: int = 0) -> int:
        try:
            value = int(raw)
        except Exception:
            value = default
        if value < min_value:
            value = min_value
        return value

    def _read_display_timezone(self):
        tz_name = str(self._config_get("display_timezone", "Asia/Shanghai")).strip()
        if not tz_name:
            tz_name = "Asia/Shanghai"

        try:
            return ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            logger.warning(f"时区无效，使用默认 Asia/Shanghai: {tz_name}")
        except Exception:
            logger.warning(f"读取时区失败，使用默认 Asia/Shanghai: {tz_name}")

        try:
            return ZoneInfo("Asia/Shanghai")
        except Exception:
            return timezone(timedelta(hours=8))

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_event_message(self, event: AstrMessageEvent):
        message = (event.message_str or "").strip()
        if not message:
            return

        lower_msg = message.lower()
        if not (
            lower_msg == "/rss"
            or lower_msg.startswith("/rss ")
            or lower_msg.startswith("/rss@")
            or lower_msg == "rss"
            or lower_msg.startswith("rss ")
        ):
            return

        try:
            tokens = shlex.split(message)
        except ValueError:
            yield event.plain_result("命令格式错误：引号未闭合。")
            return

        if not tokens:
            return

        first = tokens[0].lower()
        if first == "rss" or first == "/rss" or first.startswith("/rss@"):
            tokens = tokens[1:]
        else:
            return

        if not tokens:
            yield event.plain_result(self._help_text())
            return

        action = tokens[0].lower()
        args = tokens[1:]

        if action == "add":
            result = await self._cmd_add(event, args)
            yield event.plain_result(result)
            return

        if action in {"ls", "list"}:
            result = self._cmd_ls(event, args)
            yield event.plain_result(result)
            return

        if action == "remove":
            result = self._cmd_remove(event, args)
            yield event.plain_result(result)
            return

        if action == "change":
            result = self._cmd_change(event, args)
            yield event.plain_result(result)
            return

        if action == "get":
            results = await self._cmd_get(event, args)
            for text in results:
                yield event.plain_result(text)
            return

        yield event.plain_result(self._help_text())

    def _help_text(self) -> str:
        return (
            "RSS 命令:\n"
            "1. /rss add <origin-url> [cron exp]\n"
            "2. /rss ls\n"
            "3. /rss remove <list-index>\n"
            "4. /rss change <list-index> [cron exp]\n"
            "5. /rss get [all|list-index] [number]\n"
            f"默认 cron: {self.default_cron_expr}\n"
            "默认 number: 15"
        )

    async def _cmd_add(self, event: AstrMessageEvent, args: List[str]) -> str:
        if len(args) < 1:
            return "用法: /rss add <origin-url> [cron exp]"

        channel = event.unified_msg_origin
        url = self.rss_client.normalize_url(args[0])
        if not url:
            return "URL 格式错误，请使用 http/https 链接。"

        cron_expr = " ".join(args[1:]).strip() if len(args) > 1 else self.default_cron_expr
        if not cron_expr:
            cron_expr = self.default_cron_expr

        ok, error = validate_cron_expr(cron_expr)
        if not ok:
            return f"cron 表达式无效: {error}"

        for existing_url, _, _ in self.data_handler.list_channel_subscriptions(channel):
            if existing_url == url:
                return "该频道已订阅该 RSS 源，可用 /rss change <index> [cron exp] 调整频率。"

        try:
            title, description, items = await self.rss_client.fetch(
                url, limit=self.init_fetch_count
            )
        except Exception as exc:
            return f"订阅失败，无法拉取 RSS: {exc}"

        latest_ts = max((item.published_ts for item in items), default=0)
        recent_ids = [item.uid for item in items][: self.init_fetch_count]

        feeds = self.data_handler.data.setdefault("feeds", {})
        feed_entry = feeds.setdefault(
            url,
            {
                "title": title,
                "description": description,
                "subscribers": {},
            },
        )

        if title:
            feed_entry["title"] = title
        if description:
            feed_entry["description"] = description

        subscribers = feed_entry.setdefault("subscribers", {})
        subscribers[channel] = {
            "cron_expr": cron_expr,
            "last_update": latest_ts,
            "recent_ids": recent_ids,
        }

        self.data_handler.save_data()
        self._refresh_scheduler()

        channel_title = feed_entry.get("title") or url
        return (
            f"添加成功\n频道: {channel_title}\nURL: {url}\n"
            f"cron: {cron_expr}\n初始化拉取条数: {self.init_fetch_count}"
        )

    def _cmd_ls(self, event: AstrMessageEvent, args: List[str]) -> str:
        if args:
            return "用法: /rss ls"

        channel = event.unified_msg_origin
        subs = self.data_handler.list_channel_subscriptions(channel)
        if not subs:
            return "当前频道暂无 RSS 订阅。"

        lines = ["当前频道订阅列表:"]
        for idx, (url, feed, sub) in enumerate(subs):
            title = feed.get("title") or "未命名频道"
            cron_expr = sub.get("cron_expr") or self.default_cron_expr
            lines.append(f"{idx}. {title}")
            lines.append(f"URL: {url}")
            lines.append(f"cron: {cron_expr}")
        return "\n".join(lines)

    def _cmd_remove(self, event: AstrMessageEvent, args: List[str]) -> str:
        if len(args) != 1:
            return "用法: /rss remove <list-index>"

        idx = self._parse_index(args[0])
        if idx is None:
            return "list-index 必须是非负整数。"

        channel = event.unified_msg_origin
        subs = self.data_handler.list_channel_subscriptions(channel)
        if idx >= len(subs):
            return "索引越界，请先用 /rss ls 查看序号。"

        url, feed, _ = subs[idx]
        subscribers = feed.get("subscribers", {})
        subscribers.pop(channel, None)

        if not subscribers:
            self.data_handler.data.get("feeds", {}).pop(url, None)

        self.data_handler.save_data()
        self._refresh_scheduler()
        return "移除成功。"

    def _cmd_change(self, event: AstrMessageEvent, args: List[str]) -> str:
        if len(args) < 1:
            return "用法: /rss change <list-index> [cron exp]"

        idx = self._parse_index(args[0])
        if idx is None:
            return "list-index 必须是非负整数。"

        cron_expr = " ".join(args[1:]).strip() if len(args) > 1 else self.default_cron_expr
        if not cron_expr:
            cron_expr = self.default_cron_expr

        ok, error = validate_cron_expr(cron_expr)
        if not ok:
            return f"cron 表达式无效: {error}"

        channel = event.unified_msg_origin
        subs = self.data_handler.list_channel_subscriptions(channel)
        if idx >= len(subs):
            return "索引越界，请先用 /rss ls 查看序号。"

        _, _, sub = subs[idx]
        sub["cron_expr"] = cron_expr

        self.data_handler.save_data()
        self._refresh_scheduler()
        return f"更新成功，新的 cron: {cron_expr}"

    async def _cmd_get(self, event: AstrMessageEvent, args: List[str]) -> List[str]:
        target: str = "all"
        number = 15

        if len(args) == 1:
            target = args[0]
        elif len(args) == 2:
            target = args[0]
            parsed_number = self._parse_positive_int(args[1])
            if parsed_number is None:
                return ["number 必须是正整数。"]
            number = parsed_number
        elif len(args) > 2:
            return ["用法: /rss get [all|list-index] [number]"]

        channel = event.unified_msg_origin
        subs = self.data_handler.list_channel_subscriptions(channel)
        if not subs:
            return ["当前频道暂无 RSS 订阅。"]

        selected: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []

        if target.lower() == "all":
            selected = subs
        else:
            idx = self._parse_index(target)
            if idx is None:
                return ["目标必须是 all 或 list-index。"]
            if idx >= len(subs):
                return ["索引越界，请先用 /rss ls 查看序号。"]
            selected = [subs[idx]]

        outputs: List[str] = []
        for url, feed, _ in selected:
            title = feed.get("title") or url
            try:
                fetched_title, fetched_desc, items = await self.rss_client.fetch(url, limit=number)
            except Exception as exc:
                outputs.append(f"[{title}] 拉取失败: {exc}")
                continue

            if fetched_title:
                feed["title"] = fetched_title
            if fetched_desc:
                feed["description"] = fetched_desc

            if not items:
                outputs.append(f"[{title}] 暂无可用内容。")
                continue

            outputs.append(self._format_get_output(feed.get("title") or title, url, items, number))

        self.data_handler.save_data()
        return outputs

    def _format_get_output(self, title: str, url: str, items: List[RSSItem], number: int) -> str:
        picked_items = items[:number]
        lines = [f"来自 [{title}]: [{url}]"]
        for idx, item in enumerate(picked_items, start=1):
            lines.append(f"# {idx}. {item.title or '无标题'}")
            lines.append(f"> {self._format_item_time(item)}")
            lines.append(item.summary or "（无摘要）")
            if idx != len(picked_items):
                lines.append("")
        return "\n".join(lines)

    async def _scheduled_poll(self, url: str, channel: str):
        feeds = self.data_handler.data.get("feeds", {})
        feed = feeds.get(url)
        if not isinstance(feed, dict):
            return

        subscribers = feed.get("subscribers", {})
        if not isinstance(subscribers, dict) or channel not in subscribers:
            return

        sub = subscribers[channel]
        if not isinstance(sub, dict):
            return

        try:
            title, description, items = await self.rss_client.fetch(
                url, limit=self.poll_fetch_count
            )
        except Exception as exc:
            logger.warning(f"rss 定时拉取失败: {url} - {channel} - {exc}")
            return

        if title:
            feed["title"] = title
        if description:
            feed["description"] = description

        new_items = self._collect_new_items(items, sub)
        if not new_items:
            return

        for item in reversed(new_items):
            text = self._format_push_message(feed.get("title") or url, item)
            chain = MessageChain(chain=[Comp.Plain(text)])
            try:
                await self.context.send_message(channel, chain)
            except Exception as exc:
                logger.warning(f"rss 推送失败: {url} - {channel} - {exc}")

        self._update_subscription_checkpoint(sub, new_items)
        self.data_handler.save_data()

    def _format_push_message(self, title: str, item: RSSItem) -> str:
        lines = [f"[{title}] 有新内容", f"标题: {item.title or '无标题'}"]
        lines.append(f"时间: {self._format_item_time(item)}")
        if item.link:
            lines.append(f"链接: {item.link}")
        if item.summary:
            lines.append(f"摘要: {item.summary}")
        return "\n".join(lines)

    def _format_item_time(self, item: RSSItem) -> str:
        if item.published_ts > 0:
            try:
                return datetime.fromtimestamp(
                    item.published_ts, tz=self.display_tz
                ).strftime("%Y.%m.%d %H:%M:%S")
            except Exception:
                pass

        if item.published:
            return item.published
        return "未知时间"

    def _collect_new_items(self, items: List[RSSItem], sub: Dict[str, Any]) -> List[RSSItem]:
        recent_ids = sub.get("recent_ids", [])
        if not isinstance(recent_ids, list):
            recent_ids = []

        recent_id_set = {str(x) for x in recent_ids if isinstance(x, str) and x}
        last_update = int(sub.get("last_update") or 0)

        new_items: List[RSSItem] = []
        for item in items:
            if item.uid in recent_id_set:
                continue
            if item.published_ts and item.published_ts < last_update:
                continue
            new_items.append(item)
        return new_items

    def _update_subscription_checkpoint(self, sub: Dict[str, Any], new_items: List[RSSItem]) -> None:
        current_last = int(sub.get("last_update") or 0)
        next_last = current_last
        for item in new_items:
            if item.published_ts > next_last:
                next_last = item.published_ts

        merged: List[str] = []
        for uid in [x.uid for x in new_items] + list(sub.get("recent_ids", [])):
            if isinstance(uid, str) and uid and uid not in merged:
                merged.append(uid)

        sub["last_update"] = next_last
        sub["recent_ids"] = merged[: self.init_fetch_count]

    def _refresh_scheduler(self) -> None:
        if not self.scheduler.running:
            return

        self.scheduler.remove_all_jobs()
        feeds = self.data_handler.data.get("feeds", {})
        if not isinstance(feeds, dict):
            return

        for url, feed in feeds.items():
            if not isinstance(feed, dict):
                continue
            subscribers = feed.get("subscribers", {})
            if not isinstance(subscribers, dict):
                continue

            for channel, sub in subscribers.items():
                if not isinstance(sub, dict):
                    continue

                cron_expr = str(sub.get("cron_expr") or self.default_cron_expr)
                ok, error = validate_cron_expr(cron_expr)
                if not ok:
                    logger.warning(f"cron 无效，跳过任务: {cron_expr} ({error})")
                    continue

                trigger_kwargs = parse_cron_expr(cron_expr)
                job_id = self._job_id(url, channel)
                try:
                    self.scheduler.add_job(
                        self._scheduled_poll,
                        "cron",
                        id=job_id,
                        replace_existing=True,
                        args=[url, channel],
                        **trigger_kwargs,
                    )
                except Exception as exc:
                    logger.warning(f"添加定时任务失败: {url} - {channel} - {exc}")

    def _job_id(self, url: str, channel: str) -> str:
        raw = f"{url}|{channel}".encode("utf-8", errors="ignore")
        return "rss-" + hashlib.md5(raw).hexdigest()

    def _parse_index(self, value: str) -> Optional[int]:
        try:
            idx = int(value)
        except Exception:
            return None
        return idx if idx >= 0 else None

    def _parse_positive_int(self, value: str) -> Optional[int]:
        try:
            parsed = int(value)
        except Exception:
            return None
        return parsed if parsed > 0 else None
