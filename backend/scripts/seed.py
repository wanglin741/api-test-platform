"""seed 脚本:一键灌入 / 重置验收数据(spec 验收场景)。

内容:默认项目 + dev 环境 + 变量(项目级 / 环境级)+ 一条 2 步用例
(① POST 登录,提取 $.data.token;② GET 查询,带 {{token}} 头,断言状态码与 $.code)。
重复执行即重置:业务表全部清空后重建,种子用户保留。

用法(backend/ 目录下):
    PYTHONUTF8=1 uv run python scripts/seed.py

可选环境变量:
    APITEST_SEED_BASE_URL  被测服务地址(默认 http://127.0.0.1:9000)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.engine.dsl import parse_dsl
from app.models import Environment, Project, TestCase, TestCaseVersion, User, Variable

SEED_BASE_URL = os.environ.get("APITEST_SEED_BASE_URL", "http://127.0.0.1:9000")
SEED_USERNAME = "demo_user"
SEED_PASSWORD = "demo_pass"

DSL = {
    "dsl_version": 1,
    "config": {"variables": {}},
    "steps": [
        {
            "id": "login",
            "name": "登录提取 token",
            "type": "http",
            "request": {
                "method": "POST",
                "url": "{{base_url}}/login",
                "headers": {"Content-Type": "application/json"},
                "body": {
                    "type": "json",
                    "content": {"username": "{{username}}", "password": "{{password}}"},
                },
            },
            "extract": [
                {"name": "token", "type": "jsonpath", "expression": "$.data.token"}
            ],
            "assert": [
                {"type": "status_code", "operator": "eq", "expected": 200},
                {"type": "jsonpath", "expression": "$.code", "operator": "eq", "expected": 0},
            ],
        },
        {
            "id": "profile",
            "name": "携带 token 查询",
            "type": "http",
            "request": {
                "method": "GET",
                "url": "{{base_url}}/profile",
                "headers": {"Authorization": "Bearer {{token}}"},
            },
            "assert": [
                {"type": "status_code", "operator": "eq", "expected": 200},
                {"type": "jsonpath", "expression": "$.code", "operator": "eq", "expected": 0},
                {
                    "type": "jsonpath",
                    "expression": "$.data.username",
                    "operator": "eq",
                    "expected": SEED_USERNAME,
                },
            ],
        },
    ],
}


def main() -> None:
    parse_dsl(DSL)  # 灌入前先过一遍结构校验,坏数据不让出门

    settings = get_settings()
    engine = create_engine(settings.database_url_sync)
    with Session(engine) as session:
        # 重置:业务数据全部清空(种子用户保留)
        session.execute(
            text(
                "TRUNCATE run_step_result, run, test_case_version, test_case, "
                "variable, environment, project RESTART IDENTITY CASCADE"
            )
        )

        admin = session.execute(select(User).where(User.username == "admin")).scalar_one()

        project = Project(name="演示项目", description="spec 验收场景", settings={}, owner_id=admin.id)
        session.add(project)
        session.flush()

        environment = Environment(
            project_id=project.id,
            name="dev",
            description="本地被测服务",
            base_url=SEED_BASE_URL,
            default_headers={},
            is_default=True,
        )
        session.add(environment)
        session.flush()

        session.add_all(
            [
                # 项目级变量:跨环境共享
                Variable(project_id=project.id, environment_id=None, name="app_name", value="api-test-demo"),
                # 环境级变量:环境差异
                Variable(
                    project_id=project.id,
                    environment_id=environment.id,
                    name="base_url",
                    value=SEED_BASE_URL,
                ),
                Variable(
                    project_id=project.id,
                    environment_id=environment.id,
                    name="username",
                    value=SEED_USERNAME,
                ),
                Variable(
                    project_id=project.id,
                    environment_id=environment.id,
                    name="password",
                    value=SEED_PASSWORD,
                    is_secret=True,
                ),
            ]
        )

        case = TestCase(
            project_id=project.id,
            name="登录后查询",
            description="登录提取 token → 携带 token 查询个人信息",
            priority="P0",
            tags=["smoke", "acceptance"],
            current_version=1,
            created_by=admin.id,
        )
        session.add(case)
        session.flush()
        session.add(
            TestCaseVersion(
                test_case_id=case.id,
                version=1,
                dsl=DSL,
                change_note="seed 初始化",
                created_by=admin.id,
            )
        )
        session.commit()

        print(f"项目 {project.id} / 环境 {environment.id} / 用例 {case.id}")
        print(f"base_url = {SEED_BASE_URL}")
        print("触发执行:")
        print(
            "curl -X POST http://127.0.0.1:8000/api/v1/projects/"
            f"{project.id}/runs -H 'Content-Type: application/json' "
            f'-d \'{{"case_id": {case.id}, "environment_id": {environment.id}}}\''
        )


if __name__ == "__main__":
    main()
