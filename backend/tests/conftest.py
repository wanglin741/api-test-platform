"""全局测试夹具。"""

import pytest

from app.engine.defaults import InMemorySink


@pytest.fixture
def sink() -> InMemorySink:
    return InMemorySink()
