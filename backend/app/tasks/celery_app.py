"""Celery 应用(架构设计 §5)。

结果后端不承载业务结果(D1):执行结果全部写 PG,轮询查自己的 run 表。
"""

from celery import Celery
from celery.schedules import crontab
from celery.signals import setup_logging as celery_setup_logging
from kombu import Queue

from app.core.config import get_settings
from app.core.log import setup_logging

settings = get_settings()

celery_app = Celery("apitest", broker=settings.redis_url)

celery_app.conf.update(
    task_routes={
        "engine.execute_run": {"queue": "execution"},
        "*": {"queue": "default"},
    },
    task_queues=(Queue("default"), Queue("execution")),
    # beat 只注册一条每分钟 tick(架构设计 D5):「什么该跑」在 schedule 表里,
    # beat 不做动态调度。beat 必须单实例,多实例会重复触发 tick。
    beat_schedule={
        "dispatch-due-schedules": {
            "task": "engine.dispatch_due_schedules",
            "schedule": crontab(),  # 每分钟
        },
    },
    # 执行任务是长任务且有副作用:崩溃后重投,依赖任务入口的幂等检查
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_ignore_result=True,
    task_store_errors_even_if_ignored=True,
    task_soft_time_limit=55 * 60,
    task_time_limit=60 * 60,
    broker_connection_retry_on_startup=True,
)

# 导入任务模块完成注册
from app.tasks import engine_tasks as _engine_tasks  # noqa: E402, F401
from app.tasks import schedule_tasks as _schedule_tasks  # noqa: E402, F401


@celery_setup_logging.connect
def _configure_worker_logging(**_kwargs) -> None:
    setup_logging(settings.log_level)
