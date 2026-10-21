"""质量门禁求值(spec #35 决策 #9/#10,issue #40)。

与展示用的 `summary.pass_rate`(四舍五入 3 位)不同,门禁使用精确值
`passed / (total - skipped)`;`max_error_count` 对应步骤级 error。

无门禁时原样返回 summary —— 不写 `gate_passed` 键,客户端据此区分
「未配置」与「未通过」。求值只读 summary,不修改 run.status。
"""


def evaluate_quality_gate(
    quality_gate: dict | None, summary: dict | None
) -> dict | None:
    """在 summary 上写入 gate_passed 并返回;无门禁时原样返回。"""
    if not quality_gate:
        return summary
    result = dict(summary or {})
    executed = (result.get("total") or 0) - (result.get("skipped") or 0)
    # 无已执行步骤时沿用展示口径的 0:0 < 任何正阈值,门禁不通过
    pass_rate = (result.get("passed") or 0) / executed if executed > 0 else 0.0
    min_pass_rate = quality_gate.get("min_pass_rate")
    max_error_count = quality_gate.get("max_error_count")
    result["gate_passed"] = (
        min_pass_rate is None or pass_rate >= min_pass_rate
    ) and (max_error_count is None or (result.get("error") or 0) <= max_error_count)
    return result
