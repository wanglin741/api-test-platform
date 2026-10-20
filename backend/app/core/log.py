"""统一日志初始化(core 层:配置、安全、日志、异常处理)。

唯一入口 setup_logging():API 进程在 app.main 模块加载时调用,
worker 进程经 Celery 的 setup_logging signal 调用(有接收者时 Celery
自动跳过对 root logger 的默认接管)。幂等:重复调用不叠加 handler。
"""

import logging
from logging.config import dictConfig

_HANDLER_NAME = "apitest_stderr"


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if any(h.name == _HANDLER_NAME for h in root.handlers):
        return
    dictConfig(
        {
            "version": 1,
            # worker 里任务模块的 logger 先于 signal 创建,True 会把它们静默禁用
            "disable_existing_loggers": False,
            "root": {"level": level, "handlers": [_HANDLER_NAME]},
            "handlers": {
                _HANDLER_NAME: {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stderr",
                    "formatter": "default",
                }
            },
            "formatters": {
                "default": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}
            },
        }
    )
    # dictConfig 不设 handler.name,显式命名供幂等检查
    for h in root.handlers:
        h.name = _HANDLER_NAME
