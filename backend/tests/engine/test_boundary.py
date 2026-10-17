"""引擎边界守护:app/engine/ 禁止 import Web 框架 / 任务队列 / ORM。

设计文档《执行引擎》§1 的硬约束,用 grimp 建图静态检查,
allowlist 语义 —— 允许集之外的任何第三方依赖直接判违规,
未来引入 fastapi / celery / sqlalchemy 会被这里拦下。

配套的 import-linter 契约见 backend/.importlinter(CI 用)。
"""

import sys
from collections.abc import Iterator

import grimp
import pytest

# 引擎允许的第三方依赖(设计文档 §1:标准库、httpx、pydantic、jsonpath、orjson)
ALLOWED_THIRD_PARTY = {
    "httpx",
    "pydantic",
    "pydantic_core",
    "pydantic_settings",
    "orjson",
    "jsonpath_ng",
    "annotated_types",
}


@pytest.fixture(scope="module")
def graph() -> Iterator[grimp.ImportGraph]:
    yield grimp.build_graph("app", include_external_packages=True)


def engine_modules(graph: grimp.ImportGraph) -> list[str]:
    return [m for m in graph.modules if m == "app.engine" or m.startswith("app.engine.")]


def test_engine_package_exists(graph: grimp.ImportGraph) -> None:
    assert engine_modules(graph), "app.engine 包不存在"


def test_engine_import_boundary(graph: grimp.ImportGraph) -> None:
    stdlib = set(sys.stdlib_module_names)
    violations: list[str] = []

    for module in engine_modules(graph):
        for imported in graph.find_modules_directly_imported_by(module):
            root = imported.split(".")[0]
            if root in stdlib:
                continue
            if imported.startswith("app.engine"):
                continue
            # 引擎不得 import 应用的其他层(config 也经 context 注入)
            if imported.startswith("app."):
                violations.append(f"{module} -> {imported}(应用层耦合)")
                continue
            if root in ALLOWED_THIRD_PARTY:
                continue
            violations.append(f"{module} -> {imported}(第三方依赖不在允许集)")

    assert violations == [], "engine/ 边界违规:\n" + "\n".join(violations)
