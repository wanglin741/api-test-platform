"""JUnit XML 渲染纯函数单测(issue #41,spec #35 决策 #8)。

无 IO / 无数据库:只断言渲染输出的外部形态 —— 节点、数量、时间口径、
状态映射、转义、门禁属性与 system-out 行数上限。
"""

import datetime
import xml.etree.ElementTree as ET

import pytest

from app.junit_export import MAX_SYSTEM_OUT_LINES, CaseResult, StepSummary, render_junit

TIMESTAMP = datetime.datetime(2026, 10, 21, 12, 30, tzinfo=datetime.UTC)


def _render(cases, *, suite_name="回归计划", gate_passed=None) -> ET.Element:
    return ET.fromstring(
        render_junit(
            suite_name=suite_name,
            cases=cases,
            timestamp=TIMESTAMP,
            gate_passed=gate_passed,
        )
    )


def _case(name, status, time_ms, steps=(), classname="回归计划") -> CaseResult:
    return CaseResult(
        classname=classname, name=name, status=status, time_ms=time_ms, steps=tuple(steps)
    )


def test_plan_run_emits_one_testcase_per_case_with_suite_counts():
    root = _render(
        [
            _case(
                "登录",
                "success",
                1500,
                [StepSummary("打开", "passed", 700, 2, 2), StepSummary("提交", "passed", 800, 1, 1)],
            ),
            _case("下单", "failed", 1200, [StepSummary("创建订单", "failed", 1200, 1, 0)]),
            _case("支付", "skipped", 0),
        ]
    )

    assert root.tag == "testsuites"
    suite = root.find("testsuite")
    assert suite is not None
    assert suite.attrib["name"] == "回归计划"
    assert suite.attrib["tests"] == "3"
    assert suite.attrib["failures"] == "1"
    assert suite.attrib["errors"] == "0"
    assert suite.attrib["skipped"] == "1"
    # time 以秒为单位,保留 3 位小数(1.5 + 1.2 = 2.7)
    assert suite.attrib["time"] == "2.700"
    assert suite.attrib["timestamp"] == "2026-10-21T12:30:00+00:00"

    testcases = suite.findall("testcase")
    assert [tc.attrib["name"] for tc in testcases] == ["登录", "下单", "支付"]
    assert all(tc.attrib["classname"] == "回归计划" for tc in testcases)
    # 用例时间 = 该用例步骤耗时之和
    assert testcases[0].attrib["time"] == "1.500"
    assert testcases[1].attrib["time"] == "1.200"
    assert testcases[2].attrib["time"] == "0.000"


@pytest.mark.parametrize(
    ("status", "child_tag"),
    [
        ("success", None),
        ("failed", "failure"),
        ("error", "error"),
        ("skipped", "skipped"),
        ("stopped", "skipped"),
    ],
)
def test_status_maps_to_junit_child(status: str, child_tag: str | None):
    root = _render([_case("用例", status, 10)])
    testcase = root.find("testsuite/testcase")
    assert testcase is not None
    present = {child.tag for child in testcase}
    assert present == ({child_tag} if child_tag else set())


def test_gate_property_only_when_gate_configured():
    passed = _render([_case("用例", "success", 10)], gate_passed=True)
    prop = passed.find("testsuite/properties/property")
    assert prop is not None
    assert prop.attrib == {"name": "gate_passed", "value": "true"}

    failed = _render([_case("用例", "failed", 10)], gate_passed=False)
    assert failed.find("testsuite/properties/property").attrib["value"] == "false"

    # 未配置门禁:不写 properties 节点
    absent = _render([_case("用例", "success", 10)], gate_passed=None)
    assert absent.find("testsuite/properties") is None


def test_special_characters_are_escaped_and_round_trip():
    case_name = '<登录 & "退出">'
    step_name = "步骤 <1> & '引号'"
    xml = render_junit(
        suite_name='计划 <A> & "B"',
        cases=[_case(case_name, "failed", 10, [StepSummary(step_name, "failed", 10, 2, 1)])],
        timestamp=TIMESTAMP,
    )

    # 输出本身必须是合法 XML(转义失败会在这里抛 ParseError)
    root = ET.fromstring(xml)
    suite = root.find("testsuite")
    assert suite.attrib["name"] == '计划 <A> & "B"'
    testcase = suite.find("testcase")
    assert testcase.attrib["name"] == case_name
    system_out = testcase.find("system-out")
    assert system_out is not None
    assert step_name in system_out.text


def test_illegal_control_characters_do_not_break_xml():
    """名称来自用户输入;XML 1.0 非法控制字符必须清掉,否则报告无法解析。"""
    root = _render(
        [_case("用例\x00\x07", "success", 1, [StepSummary("步骤\x01", "passed", 1)])]
    )
    testcase = root.find("testsuite/testcase")
    assert testcase.attrib["name"] == "用例"
    assert "步骤" in testcase.find("system-out").text


def test_system_out_lists_step_summary_with_assertion_counts():
    root = _render(
        [_case("登录", "failed", 10, [StepSummary("提交", "failed", 250, 3, 2)])]
    )
    text = root.find("testsuite/testcase/system-out").text
    assert "提交" in text
    assert "failed" in text
    assert "250ms" in text
    assert "2/3" in text


def test_system_out_is_capped_at_500_lines():
    steps = [StepSummary(f"步骤{i}", "passed", 1, 1, 1) for i in range(MAX_SYSTEM_OUT_LINES + 100)]
    root = _render([_case("大用例", "success", 600, steps)])

    text = root.find("testsuite/testcase/system-out").text
    lines = text.splitlines()
    assert len(lines) == MAX_SYSTEM_OUT_LINES
    assert "步骤499" in text
    assert "步骤500" not in text


def test_single_case_run_uses_run_duration_not_step_sum():
    root = _render(
        [_case("登录", "success", 2500, [StepSummary("打开", "passed", 1000, 0, 0)])],
        suite_name="登录",
    )
    testcase = root.find("testsuite/testcase")
    assert testcase.attrib["time"] == "2.500"
    assert testcase.attrib["name"] == "登录"
    assert root.find("testsuite").attrib["tests"] == "1"


def test_stopped_case_counts_as_skipped_at_suite_level():
    root = _render([_case("未执行", "stopped", 0)])
    assert root.find("testsuite").attrib["skipped"] == "1"
    assert root.find("testsuite").attrib["failures"] == "0"
    assert root.find("testsuite").attrib["errors"] == "0"
