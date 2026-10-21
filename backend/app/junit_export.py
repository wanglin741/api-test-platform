"""JUnit XML 渲染(issue #41,spec #35 决策 #8)。

纯函数:输入已解析好的用例/步骤摘要,输出 XML 字符串。不碰 ORM、
不读响应体大字段 —— 端点只把轻量列喂进来,渲染本身可纯函数单测。
"""

import datetime
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

MAX_SYSTEM_OUT_LINES = 500
SKIPPED_STATUSES = ("skipped", "stopped")
# XML 1.0 不允许的控制字符:名称来自用户输入,不清掉会让整个报告解析失败
_ILLEGAL_XML_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


@dataclass(frozen=True)
class StepSummary:
    """system-out 的一行:名称/状态/耗时/断言通过数,不含请求响应体。"""

    name: str
    status: str
    duration_ms: int
    assertion_total: int = 0
    assertion_passed: int = 0


@dataclass(frozen=True)
class CaseResult:
    classname: str
    name: str
    status: str
    time_ms: int
    steps: tuple[StepSummary, ...] = field(default_factory=tuple)


def _seconds(ms: int) -> str:
    return f"{ms / 1000:.3f}"


def _clean(text: str) -> str:
    return _ILLEGAL_XML_CHARS.sub("", text)


def _append_status(testcase: ET.Element, status: str) -> None:
    """终态映射:success 无子节点;failed/error 各自节点;skipped/stopped → skipped。"""
    if status == "failed":
        ET.SubElement(testcase, "failure")
    elif status == "error":
        ET.SubElement(testcase, "error")
    elif status in SKIPPED_STATUSES:
        ET.SubElement(testcase, "skipped")


def _system_out_text(steps: tuple[StepSummary, ...]) -> str | None:
    if not steps:
        return None
    lines = [
        _clean(
            f"[{step.status}] {step.name} ({step.duration_ms}ms, "
            f"断言 {step.assertion_passed}/{step.assertion_total})"
        )
        for step in steps[:MAX_SYSTEM_OUT_LINES]
    ]
    return "\n".join(lines)


def render_junit(
    *,
    suite_name: str,
    cases: list[CaseResult],
    timestamp: datetime.datetime,
    gate_passed: bool | None = None,
) -> str:
    """渲染一个 run 为 `<testsuites>`(一个 run 一个 `<testsuite>`)。

    `gate_passed` 为 None 表示未配置门禁,不输出 `<properties>`(spec 决策 #9)。
    ElementTree 负责转义,输出是合法 XML。
    """
    root = ET.Element("testsuites")
    suite = ET.SubElement(
        root,
        "testsuite",
        {
            "name": _clean(suite_name),
            "tests": str(len(cases)),
            "failures": str(sum(c.status == "failed" for c in cases)),
            "errors": str(sum(c.status == "error" for c in cases)),
            "skipped": str(sum(c.status in SKIPPED_STATUSES for c in cases)),
            "time": _seconds(sum(c.time_ms for c in cases)),
            "timestamp": timestamp.isoformat(timespec="seconds"),
        },
    )

    if gate_passed is not None:
        properties = ET.SubElement(suite, "properties")
        ET.SubElement(
            properties,
            "property",
            {"name": "gate_passed", "value": "true" if gate_passed else "false"},
        )

    for case in cases:
        testcase = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": _clean(case.classname),
                "name": _clean(case.name),
                "time": _seconds(case.time_ms),
            },
        )
        _append_status(testcase, case.status)
        text = _system_out_text(case.steps)
        if text is not None:
            ET.SubElement(testcase, "system-out").text = text

    return ET.tostring(root, encoding="unicode", xml_declaration=True)
