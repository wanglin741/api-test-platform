"""变量作用域与 `{{var}}` 解析。

设计文档《执行引擎》§3:
- 5 层作用域,从低到高:内置函数 / 项目级 / 环境级 / 用例级 / 运行时提取
- 解析是递归展开(等价于定点迭代):变量值本身可引用其他变量,
  引用链用于显式检测循环引用,深度上限 5 兜底极深的非循环链
- 整体占位符才做类型推断:字符串恰好是单个 `{{var}}` 时保留原生类型
- `$env.` 只允许白名单前缀,防止用例读到平台自身凭据

本模块不 import 任何框架。
"""

import os
import random
import re
import string
import time
import uuid
from typing import Any

_PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_ESCAPED = re.compile(r"\\(\{\{[^{}]+\}\})")
_RANDOM_STRING = re.compile(r"^\$randomString\((\d+)\)$")

MAX_RESOLVE_DEPTH = 5


def compose_scope_variables(
    rows, current_environment_id: int | None, base_url: str | None
) -> tuple[dict, dict]:
    """项目级/环境级变量合成 —— 全平台唯一实现,worker 与单步调试共用。

    rows 是 (name, value, environment_id) 三元组:environment_id 为 None 归项目级,
    等于 current_environment_id 的归环境级,其余环境对本项目不可见;
    base_url 兜底只在环境配置了它且变量未显式定义 base_url 时注入(显式优先)。

    两处调用:app.tasks.engine_tasks._load_variables(同步 Session,真实执行)与
    app.api.v1.cases._load_scope_variables(AsyncSession,单步调试)。
    变量合成规则见执行引擎设计 §3 —— 调整规则只改这里,不得在调用侧自创。
    """
    project_vars: dict = {}
    env_vars: dict = {}
    for name, value, environment_id in rows:
        if environment_id is None:
            project_vars[name] = value
        elif environment_id == current_environment_id:
            env_vars[name] = value
    if base_url:
        env_vars.setdefault("base_url", base_url)
    return project_vars, env_vars


def stringify(value: Any) -> str:
    """变量值转字符串。bool 走 JSON 语义(true/false),None 为空串。"""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return ""
    return str(value)


class VariableResolutionError(Exception):
    """变量解析失败的基类。请求构建阶段遇到它 → 步骤 skipped。"""


class UndefinedVariableError(VariableResolutionError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"未定义变量: {name}")


class VariableCycleError(VariableResolutionError):
    def __init__(self, chain: tuple[str, ...]) -> None:
        self.chain = chain
        super().__init__("循环引用: " + " -> ".join(chain))


class VariableDepthError(VariableResolutionError):
    def __init__(self, name: str) -> None:
        super().__init__(f"变量解析深度超过 {MAX_RESOLVE_DEPTH} 层(自 {name} 起)")


class EnvVarNotAllowedError(VariableResolutionError):
    def __init__(self, name: str, prefixes: tuple[str, ...]) -> None:
        self.name = name
        joined = " / ".join(prefixes)
        super().__init__(f"环境变量 {name} 不在白名单内(允许前缀: {joined})")


class VariableScope:
    """一次用例执行的变量作用域。层与层之间后者覆盖前者。"""

    def __init__(
        self,
        *,
        project: dict[str, Any] | None = None,
        environment: dict[str, Any] | None = None,
        case: dict[str, Any] | None = None,
        env_prefixes: tuple[str, ...] = ("TEST_",),
    ) -> None:
        # 从低到高排列;runtime 层初始为空,提取器写入后覆盖更低的层
        self._layers: list[dict[str, Any]] = [
            dict(project or {}),
            dict(environment or {}),
            dict(case or {}),
            {},
        ]
        self._env_prefixes = env_prefixes

    def set(self, name: str, value: Any) -> None:
        """运行时注入(提取器结果 / 循环迭代变量)。"""
        self._layers[-1][name] = value

    def lookup(self, name: str) -> Any:
        for layer in reversed(self._layers):
            if name in layer:
                return layer[name]
        raise UndefinedVariableError(name)

    def resolve(self, value: Any, _chain: tuple[str, ...] = ()) -> Any:
        if isinstance(value, dict):
            return {k: self.resolve(v, _chain) for k, v in value.items()}
        if isinstance(value, list):
            return [self.resolve(v, _chain) for v in value]
        if not isinstance(value, str):
            return value
        return self._resolve_str(value, _chain)

    def _resolve_str(self, text: str, chain: tuple[str, ...]) -> Any:
        if not _PLACEHOLDER.search(text):
            return text

        # 整体占位符 → 类型推断,保留变量原生类型(int/bool/dict/…)
        whole = _PLACEHOLDER.fullmatch(text)
        if whole:
            return self._resolve_name(whole.group(1), chain)

        # 先保护转义片段,再展开占位符,最后还原
        protected: list[str] = []

        def _protect(m: re.Match[str]) -> str:
            protected.append(m.group(1))
            return f"\x00{len(protected) - 1}\x00"

        text = _ESCAPED.sub(_protect, text)
        text = _PLACEHOLDER.sub(lambda m: stringify(self._resolve_name(m.group(1), chain)), text)
        text = re.sub(r"\x00(\d+)\x00", lambda m: protected[int(m.group(1))], text)
        return text

    def _resolve_name(self, name: str, chain: tuple[str, ...]) -> Any:
        if name in chain:
            raise VariableCycleError(chain + (name,))
        if len(chain) >= MAX_RESOLVE_DEPTH:
            raise VariableDepthError(name)
        if name.startswith("$"):
            return self._builtin(name, chain)
        value = self.lookup(name)
        if isinstance(value, (str, dict, list)) and value:
            return self.resolve(value, chain + (name,))
        return value

    def _builtin(self, name: str, chain: tuple[str, ...]) -> Any:
        if name == "$uuid":
            return str(uuid.uuid4())
        if name == "$timestamp":
            return int(time.time())
        if name == "$timestamp_ms":
            return int(time.time() * 1000)
        if name == "$randomInt":
            return random.randint(0, 1_000_000)
        if m := _RANDOM_STRING.match(name):
            n = int(m.group(1))
            alphabet = string.ascii_letters + string.digits
            return "".join(random.choices(alphabet, k=n))
        if name.startswith("$env."):
            return self._env_var(name[len("$env.") :])
        raise UndefinedVariableError(name)

    def _env_var(self, name: str) -> str:
        if not any(name.startswith(p) for p in self._env_prefixes):
            raise EnvVarNotAllowedError(name, self._env_prefixes)
        value = os.environ.get(name)
        if value is None:
            raise UndefinedVariableError(f"$env.{name}")
        return value
