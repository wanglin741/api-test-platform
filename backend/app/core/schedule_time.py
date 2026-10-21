"""调度时间纯函数(spec #35:校验与 compute_next,无 IO、无数据库)。

- cron 仅接受标准 5 段(分 时 日 月 周);6 段带秒的形态明确不支持。
- 时区必须是合法 IANA 名称(Windows 依赖 tzdata 包)。
- next_run_at 存 UTC;cron 按 schedule 自己的时区解释。
- misfire 基准 = max(next_run_at, now):停机错过的时点不补跑,只算下一个未来时间。
"""

import datetime
from zoneinfo import ZoneInfo

from croniter import croniter

from app.core.errors import AppError


class CronInvalidError(AppError):
    status_code = 422
    code = "cron_invalid"


class TimezoneInvalidError(AppError):
    status_code = 422
    code = "timezone_invalid"


def validate_cron(expression: str) -> None:
    """仅接受 5 段 cron;非法抛 cron_invalid(422)。"""
    if len(expression.split()) != 5:
        raise CronInvalidError("cron 表达式必须为 5 段(分 时 日 月 周)", details={"cron": expression})
    if not croniter.is_valid(expression):
        raise CronInvalidError("cron 表达式非法", details={"cron": expression})


def validate_timezone(name: str) -> None:
    """必须是合法 IANA 时区名;非法抛 timezone_invalid(422)。"""
    try:
        ZoneInfo(name)
    except Exception:
        raise TimezoneInvalidError("时区必须是合法 IANA 名称", details={"timezone": name}) from None


def compute_next(
    expression: str,
    timezone: str,
    base: datetime.datetime,
    previous: datetime.datetime | None = None,
) -> datetime.datetime:
    """从基准时间算下一个触发点,返回 aware UTC。

    基准 = max(previous, base):previous 是持久化的旧 next_run_at,
    停机重启后用旧值防止「now 已越过多个时点」时的雪崩重算 —— 只取下一个未来时点。
    base/previous 允许 naive(按 UTC 处理,与库内 TIMESTAMPTZ 语义一致)。
    """
    validate_cron(expression)
    validate_timezone(timezone)
    if base.tzinfo is None:
        base = base.replace(tzinfo=datetime.UTC)
    if previous is not None and previous.tzinfo is None:
        previous = previous.replace(tzinfo=datetime.UTC)
    anchor = base if previous is None or previous <= base else previous
    tz = ZoneInfo(timezone)
    local = anchor.astimezone(tz)
    nxt = croniter(expression, local).get_next(datetime.datetime)
    return nxt.astimezone(datetime.UTC)
