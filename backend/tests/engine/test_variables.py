"""变量解析:5 层作用域、定点/链式解析、循环引用、内置函数、类型推断。

按设计文档《执行引擎》§3 的解析规则逐条对应。
"""

import re
import uuid

import pytest

from app.engine.variables import (
    EnvVarNotAllowedError,
    UndefinedVariableError,
    VariableCycleError,
    VariableDepthError,
    VariableScope,
    compose_scope_variables,
)


def make_scope(project=None, environment=None, case=None, prefixes=("TEST_",)) -> VariableScope:
    return VariableScope(
        project=project or {},
        environment=environment or {},
        case=case or {},
        env_prefixes=prefixes,
    )


class TestLayering:
    def test_resolves_from_case_level(self) -> None:
        scope = make_scope(case={"base_url": "https://api.example.com"})
        assert scope.resolve("{{base_url}}/v1") == "https://api.example.com/v1"

    def test_priority_case_over_environment_over_project(self) -> None:
        scope = make_scope(
            project={"token": "project"},
            environment={"token": "env"},
            case={"token": "case"},
        )
        assert scope.resolve("{{token}}") == "case"

    def test_priority_environment_over_project(self) -> None:
        scope = make_scope(project={"token": "project"}, environment={"token": "env"})
        assert scope.resolve("{{token}}") == "env"

    def test_falls_back_to_project_when_only_there(self) -> None:
        scope = make_scope(project={"token": "project"})
        assert scope.resolve("{{token}}") == "project"


class TestChainedResolution:
    def test_variable_referencing_variable(self) -> None:
        scope = make_scope(case={"base_url": "https://api.example.com", "host": "{{base_url}}/v1"})
        assert scope.resolve("{{host}}/users") == "https://api.example.com/v1/users"

    def test_three_level_chain(self) -> None:
        scope = make_scope(case={"a": "{{b}}", "b": "{{c}}", "c": "leaf"})
        assert scope.resolve("{{a}}") == "leaf"


class TestCycleDetection:
    def test_direct_self_reference(self) -> None:
        scope = make_scope(case={"a": "{{a}}"})
        with pytest.raises(VariableCycleError) as exc:
            scope.resolve("{{a}}")
        assert "循环引用" in str(exc.value)
        assert "a -> a" in str(exc.value)

    def test_indirect_cycle(self) -> None:
        scope = make_scope(case={"a": "{{b}}", "b": "{{c}}", "c": "{{a}}"})
        with pytest.raises(VariableCycleError) as exc:
            scope.resolve("{{a}}")
        assert "循环引用: a -> b -> c -> a" in str(exc.value)

    def test_deep_chain_beyond_limit_is_depth_error_not_hang(self) -> None:
        case = {f"v{i}": "{{" + f"v{i + 1}" + "}}" for i in range(10)}
        case["v10"] = "leaf"
        scope = make_scope(case=case)
        with pytest.raises(VariableDepthError):
            scope.resolve("{{v0}}")


class TestUndefined:
    def test_undefined_raises_with_name(self) -> None:
        with pytest.raises(UndefinedVariableError) as exc:
            make_scope().resolve("{{token}}")
        assert "token" in str(exc.value)

    def test_undefined_inside_interpolation(self) -> None:
        with pytest.raises(UndefinedVariableError):
            make_scope(case={"x": "1"}).resolve("url/{{missing}}/tail")


class TestEscaping:
    def test_escaped_placeholder_stays_literal(self) -> None:
        scope = make_scope(case={"name": "alice"})
        assert scope.resolve(r"\{{name}}") == "{{name}}"

    def test_escaped_and_real_placeholder_mixed(self) -> None:
        scope = make_scope(case={"name": "alice"})
        assert scope.resolve(r"\{{name}} {{name}}") == "{{name}} alice"


class TestBuiltins:
    def test_uuid(self) -> None:
        value = make_scope().resolve("{{$uuid}}")
        assert str(uuid.UUID(value)) == value  # 合法 UUID4 字符串

    def test_timestamp_is_unix_seconds(self) -> None:
        import time

        value = make_scope().resolve("{{$timestamp}}")
        assert abs(int(value) - time.time()) < 5

    def test_timestamp_ms(self) -> None:
        import time

        value = make_scope().resolve("{{$timestamp_ms}}")
        assert abs(int(value) - time.time() * 1000) < 500

    def test_random_int(self) -> None:
        value = make_scope().resolve("{{$randomInt}}")
        assert isinstance(value, int)
        assert 0 <= value <= 1_000_000

    def test_random_string_with_length(self) -> None:
        value = make_scope().resolve("{{$randomString(12)}}")
        assert isinstance(value, str)
        assert len(value) == 12
        assert re.fullmatch(r"[A-Za-z0-9]+", value)


class TestEnvWhitelist:
    def test_whitelisted_prefix_readable(self, monkeypatch) -> None:
        monkeypatch.setenv("TEST_API_KEY", "sk-123")
        assert make_scope().resolve("{{$env.TEST_API_KEY}}") == "sk-123"

    def test_non_whitelisted_prefix_rejected(self, monkeypatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgres://secret")
        with pytest.raises(EnvVarNotAllowedError) as exc:
            make_scope().resolve("{{$env.DATABASE_URL}}")
        assert "DATABASE_URL" in str(exc.value)

    def test_whitelist_prefix_configurable(self, monkeypatch) -> None:
        monkeypatch.setenv("SECRET_", "x")  # noqa: S105 - 仅测试白名单匹配
        monkeypatch.setenv("SECRET_TOKEN", "tok")
        scope = make_scope(prefixes=("SECRET_",))
        assert scope.resolve("{{$env.SECRET_TOKEN}}") == "tok"

    def test_missing_env_var_is_undefined(self, monkeypatch) -> None:
        monkeypatch.delenv("TEST_NOPE", raising=False)
        with pytest.raises(UndefinedVariableError):
            make_scope().resolve("{{$env.TEST_NOPE}}")


class TestTypeInference:
    def test_whole_placeholder_preserves_int(self) -> None:
        assert make_scope(case={"user_id": 5}).resolve("{{user_id}}") == 5

    def test_whole_placeholder_preserves_bool(self) -> None:
        assert make_scope(case={"flag": True}).resolve("{{flag}}") is True

    def test_partial_interpolation_stringifies_bool_as_json(self) -> None:
        assert make_scope(case={"flag": True}).resolve("flag={{flag}}") == "flag=true"

    def test_partial_interpolation_stringifies_int(self) -> None:
        assert make_scope(case={"n": 5}).resolve("id-{{n}}") == "id-5"

    def test_whole_placeholder_preserves_dict(self) -> None:
        payload = {"a": 1}
        assert make_scope(case={"payload": payload}).resolve("{{payload}}") == payload


class TestRecursiveStructures:
    def test_dict_values_resolved(self) -> None:
        scope = make_scope(case={"token": "abc", "body": {"t": "{{token}}", "n": 1}})
        assert scope.resolve("{{body}}") == {"t": "abc", "n": 1}

    def test_list_values_resolved(self) -> None:
        scope = make_scope(case={"token": "abc", "items": ["{{token}}", "x"]})
        assert scope.resolve("{{items}}") == ["abc", "x"]


class TestRuntimeLayer:
    def test_runtime_extraction_overrides_case(self) -> None:
        scope = make_scope(case={"token": "stale"})
        scope.set("token", "fresh")
        assert scope.resolve("{{token}}") == "fresh"

    def test_runtime_new_variable_visible(self) -> None:
        scope = make_scope()
        scope.set("token", "fresh")
        assert scope.resolve("Bearer {{token}}") == "Bearer fresh"


class TestComposeScopeVariables:
    """worker(真实执行)与单步调试共用的变量合成规则 —— 规则只在这一份。"""

    def test_project_and_current_env_classified(self) -> None:
        rows = [("token", "p", None), ("host", "dev", 1), ("host", "prod", 2)]
        project, env = compose_scope_variables(rows, 1, None)
        assert project == {"token": "p"}
        assert env == {"host": "dev"}

    def test_base_url_fallback_only_when_undefined(self) -> None:
        _, env = compose_scope_variables([], 1, "http://from-env")
        assert env == {"base_url": "http://from-env"}

        _, env = compose_scope_variables([("base_url", "http://explicit", 1)], 1, "http://from-env")
        assert env == {"base_url": "http://explicit"}
