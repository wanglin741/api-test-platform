"""DSL 加载与校验:结构合法性、dsl_version 契约、字段默认值。

DSL 是执行的唯一事实来源(设计文档《执行引擎》§2)。MVP 只支持 http 步骤。
"""

import pytest
from pydantic import ValidationError

from app.engine.dsl import parse_dsl

MINIMAL = {
    "dsl_version": 1,
    "steps": [
        {
            "id": "s1",
            "name": "登录",
            "type": "http",
            "request": {"method": "POST", "url": "{{base_url}}/login"},
        }
    ],
}


class TestValidDsl:
    def test_minimal_http_case(self) -> None:
        dsl = parse_dsl({**MINIMAL, "config": {"variables": {"base_url": "https://x"}}})

        assert dsl.dsl_version == 1
        assert dsl.config.variables == {"base_url": "https://x"}
        assert len(dsl.steps) == 1
        step = dsl.steps[0]
        assert step.id == "s1"
        assert step.request is not None
        assert step.request.method == "POST"
        assert step.request.url == "{{base_url}}/login"

    def test_defaults_applied(self) -> None:
        dsl = parse_dsl(MINIMAL)

        assert dsl.config.timeout_ms == 10_000
        assert dsl.config.fail_fast is False
        assert dsl.steps[0].enabled is True
        assert dsl.steps[0].request.follow_redirects is True
        assert dsl.steps[0].request.verify_ssl is True

    def test_request_body_variants_parse(self) -> None:
        for body_type, content in [
            ("json", '{"user": "{{username}}"}'),
            ("raw", "hello"),
            ("form-data", {"a": "1"}),
            ("urlencoded", {"b": "2"}),
        ]:
            doc = {
                **MINIMAL,
                "steps": [
                    {
                        **MINIMAL["steps"][0],
                        "request": {
                            "method": "POST",
                            "url": "/x",
                            "body": {"type": body_type, "content": content},
                        },
                    }
                ],
            }
            dsl = parse_dsl(doc)
            assert dsl.steps[0].request.body.type == body_type
            assert dsl.steps[0].request.body.content == content

    def test_extract_and_assert_fields_accept(self) -> None:
        doc = {
            **MINIMAL,
            "steps": [
                {
                    **MINIMAL["steps"][0],
                    "extract": [
                        {"name": "token", "type": "jsonpath", "expression": "$.data.token"}
                    ],
                    "assert": [{"type": "status_code", "operator": "eq", "expected": 200}],
                }
            ],
        }
        dsl = parse_dsl(doc)
        assert dsl.steps[0].extract[0].name == "token"
        assert dsl.steps[0].assertions[0].expected == 200


class TestInvalidDsl:
    def test_missing_dsl_version_rejected(self) -> None:
        doc = {k: v for k, v in MINIMAL.items() if k != "dsl_version"}
        with pytest.raises(ValidationError) as exc:
            parse_dsl(doc)
        assert "dsl_version" in str(exc.value)

    def test_unknown_dsl_version_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_dsl({**MINIMAL, "dsl_version": 99})

    def test_empty_steps_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc:
            parse_dsl({**MINIMAL, "steps": []})
        assert "steps" in str(exc.value)

    def test_unsupported_step_type_rejected(self) -> None:
        doc = {**MINIMAL, "steps": [{**MINIMAL["steps"][0], "type": "loop"}]}
        with pytest.raises(ValidationError) as exc:
            parse_dsl(doc)
        assert "type" in str(exc.value)

    def test_request_without_url_rejected_with_location(self) -> None:
        doc = {
            **MINIMAL,
            "steps": [{**MINIMAL["steps"][0], "request": {"method": "GET"}}],
        }
        with pytest.raises(ValidationError) as exc:
            parse_dsl(doc)
        assert "url" in str(exc.value)

    def test_invalid_body_type_rejected(self) -> None:
        doc = {
            **MINIMAL,
            "steps": [
                {
                    **MINIMAL["steps"][0],
                    "request": {
                        "method": "POST",
                        "url": "/x",
                        "body": {"type": "binary", "content": "x"},
                    },
                }
            ],
        }
        with pytest.raises(ValidationError):
            parse_dsl(doc)
