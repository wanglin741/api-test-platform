"""setup_logging 外部行为:幂等、级别可调。"""

import logging

from app.core.log import setup_logging

_HANDLER_NAME = "apitest_stderr"


def _own_handlers() -> list[logging.Handler]:
    return [h for h in logging.getLogger().handlers if h.name == _HANDLER_NAME]


def test_setup_logging_idempotent() -> None:
    setup_logging()
    count = len(_own_handlers())

    setup_logging()

    assert len(_own_handlers()) == count == 1


def test_setup_logging_applies_level() -> None:
    # 先摘掉可能已存在的 handler,允许重新配置
    root = logging.getLogger()
    root.handlers[:] = [h for h in root.handlers if h.name != _HANDLER_NAME]

    setup_logging("WARNING")

    assert root.level == logging.WARNING
    assert len(_own_handlers()) == 1
