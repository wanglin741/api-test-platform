"""cron/时区纯函数单测(spec #35 测试缝 3:无 IO、无数据库,固定基准时间)。

覆盖:5/6 段、非法时区、上海时区换算、UTC 存储、misfire 基准、DST 冒烟。
"""

import datetime

import pytest

from app.core.errors import AppError
from app.core.schedule_time import compute_next, validate_cron, validate_timezone

BASE = datetime.datetime(2026, 10, 21, 10, 30, 0, tzinfo=datetime.UTC)


def _utc(y, m, d, hh, mm):
    return datetime.datetime(y, m, d, hh, mm, tzinfo=datetime.UTC)


# ---------- cron 校验 ----------


@pytest.mark.parametrize(
    ("expr", "ok"),
    [
        ("* * * * *", True),
        ("0 2 * * *", True),
        ("30 8 * * 1-5", True),
        ("0 0 1 1 *", True),
        ("*/5 * * * *", True),
        ("60 * * * *", False),  # 分钟越界
        ("* 25 * * *", False),  # 小时越界
        ("* * * * 8", False),  # 周越界
        ("not a cron", False),
        ("", False),
    ],
)
def test_validate_cron(expr, ok):
    if ok:
        validate_cron(expr)
    else:
        with pytest.raises(AppError) as exc:
            validate_cron(expr)
        assert exc.value.code == "cron_invalid"
        assert exc.value.status_code == 422


def test_validate_cron_rejects_six_fields():
    """6 段(带秒)明确不支持,不静默截断。"""
    with pytest.raises(AppError) as exc:
        validate_cron("0 * * * * *")
    assert exc.value.code == "cron_invalid"


# ---------- 时区校验 ----------


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("Asia/Shanghai", True),
        ("UTC", True),
        ("America/New_York", True),
        ("Mars/Phobos", False),
        ("", False),
        ("UTC+8", False),
    ],
)
def test_validate_timezone(name, ok):
    if ok:
        validate_timezone(name)
    else:
        with pytest.raises(AppError) as exc:
            validate_timezone(name)
        assert exc.value.code == "timezone_invalid"
        assert exc.value.status_code == 422


# ---------- compute_next ----------


def test_compute_next_shanghai_conversion():
    """「每天 09:00 上海时间」从 2026-10-21 10:30 UTC 基准 → 明天上海 09:00 = UTC 01:00。

    上海无 DST,固定 UTC+8:worked example 独立于实现。
    """
    nxt = compute_next("0 9 * * *", "Asia/Shanghai", BASE)
    assert nxt == _utc(2026, 10, 22, 1, 0)


def test_compute_next_returns_utc_and_after_base():
    nxt = compute_next("*/30 * * * *", "Asia/Shanghai", BASE)
    assert nxt.tzinfo == datetime.UTC
    assert nxt > BASE
    assert nxt == _utc(2026, 10, 21, 11, 0)  # 11:00 上海 19:00


def test_compute_next_every_minute():
    assert compute_next("* * * * *", "UTC", BASE) == _utc(2026, 10, 21, 10, 31)


def test_compute_next_misfire_anchor_uses_max_of_previous_and_base():
    """misfire 基准:max(next_run_at, now)。

    旧 next_run_at 在过去(停机错过),不遍历中间时点,取基准之后第一个时点。
    """
    stale = _utc(2026, 10, 20, 1, 0)  # 昨天的下一次,已过期
    nxt = compute_next("0 9 * * *", "Asia/Shanghai", BASE, previous=stale)
    assert nxt == _utc(2026, 10, 22, 1, 0)

    # previous 在未来(now 未到):以旧 next_run_at 为准 —— 该跑的时点还在
    future = _utc(2026, 10, 22, 1, 0)
    nxt2 = compute_next("0 9 * * *", "Asia/Shanghai", BASE, previous=future)
    assert nxt2 == _utc(2026, 10, 23, 1, 0)


def test_compute_next_accepts_naive_datetimes_as_utc():
    """库取出的 datetime 可能 naive(无 tzinfo),约定按 UTC 处理。"""
    naive_base = datetime.datetime(2026, 10, 21, 10, 30)
    assert compute_next("* * * * *", "UTC", naive_base) == _utc(2026, 10, 21, 10, 31)


def test_compute_next_dst_smoke():
    """DST 冒烟:美国东部 2026 春令时(3 月第二个周日,11-07 后 2:00→3:00)。

    结果 aware 且晚于基准即可,不承诺跳变时刻的取舍(spec 已声明)。
    """
    before = _utc(2026, 3, 7, 12, 0)
    nxt = compute_next("0 2 * * *", "America/New_York", before)
    assert nxt.tzinfo == datetime.UTC
    assert nxt > before
