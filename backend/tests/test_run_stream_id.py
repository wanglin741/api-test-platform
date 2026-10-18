"""SSE 游标(Last-Event-ID)解析:坏值必须回退快照路径,不能炸掉生成器。

_parse_stream_id 在流生成器的第一行被调用:此时响应已开始,异常无法
转成 400/500,客户端只会拿到一个无声关闭的流。isdigit() 对非 ASCII
数字字符(如 '²')也为真而 int() 抛 ValueError,必须显式挡下。
"""

from app.api.v1.runs import _parse_stream_id


def test_parses_ms_seq_pair() -> None:
    assert _parse_stream_id("1737000000123-7") == (1737000000123, 7)


def test_structural_garbage_returns_none() -> None:
    assert _parse_stream_id("") is None
    assert _parse_stream_id("abc") is None
    assert _parse_stream_id("1-") is None
    assert _parse_stream_id("-1") is None
    assert _parse_stream_id("1-2-3") is None  # 序号段含 '-':拒绝,走快照回退


def test_non_ascii_digits_return_none() -> None:
    # isdigit() 为真但 int() 会抛 ValueError 的输入,必须在解析层拒绝
    assert _parse_stream_id("²-0") is None
    assert _parse_stream_id("1-²") is None
