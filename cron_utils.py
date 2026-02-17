from typing import Dict, Tuple

from apscheduler.triggers.cron import CronTrigger


def normalize_day_of_week(value: str) -> str:
    day = (value or "").strip()
    if day in {"0-7", "1-7"}:
        return "*"
    if day == "7":
        return "0"
    return day


def parse_cron_expr(cron_expr: str) -> Dict[str, str]:
    fields = [x for x in cron_expr.split(" ") if x.strip()]
    if len(fields) == 5:
        return {
            "minute": fields[0],
            "hour": fields[1],
            "day": fields[2],
            "month": fields[3],
            "day_of_week": normalize_day_of_week(fields[4]),
        }
    if len(fields) == 6:
        return {
            "second": fields[0],
            "minute": fields[1],
            "hour": fields[2],
            "day": fields[3],
            "month": fields[4],
            "day_of_week": normalize_day_of_week(fields[5]),
        }
    raise ValueError("cron 仅支持 5 或 6 段")


def validate_cron_expr(cron_expr: str) -> Tuple[bool, str]:
    try:
        kwargs = parse_cron_expr(cron_expr)
        CronTrigger(**kwargs)
        return True, ""
    except Exception as exc:
        return False, str(exc)
