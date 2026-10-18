"""执行进度事件名与过滤白名单(架构设计 §6 / API设计 §5)。

纯常量模块,无任何依赖:引擎(不 import 框架/Redis)、任务层与 API 层
三方共用同一套名字,不出现拼写漂移 —— 发事件的一方必须 import 这里的
常量,不许写字面量。

当前已上线:RUN_STARTED / RUN_FINISHED(任务层)、STEP_FINISHED(引擎)、
TERMINAL_EVENT(SSE 端点收尾)。其余为已定契约、随后续变更启用,
载荷形状以设计文档为准。
"""

RUN_STARTED = "run.started"
STEP_STARTED = "step.started"
STEP_FINISHED = "step.finished"
RUN_PROGRESS = "run.progress"
RUN_FINISHED = "run.finished"
RUN_SNAPSHOT = "run.snapshot"

# 执行终态事件:任务层专属 —— started_at 取数据库时间、summary 含断言统计,
# 引擎不掌握这些事实(引擎只发逐步事件)
TERMINAL_EVENT = RUN_FINISHED

# ?events=finished 白名单:只订阅完成类事件,过程事件(开始/进度)被过滤
FINISHED_FILTER_EVENTS = frozenset({STEP_FINISHED, RUN_FINISHED})
