/**
 * 报告侧格式化工具(#42):trigger_mode 中文映射、门禁标签判定、
 * JUnit 附件名与「文本 → Blob」下载。纯函数/薄 DOM,便于组件与 store 复用。
 */

const TRIGGER_MODE_LABELS: Record<string, string> = {
  manual: '手动',
  schedule: '定时',
  openapi: 'OpenAPI',
  ci: 'CI',
}

/** 未知来源原样展示,不吞信息。 */
export function triggerModeLabel(mode: string): string {
  return TRIGGER_MODE_LABELS[mode] ?? mode
}

/** `summary.gate_passed` 键存在才是配置了门禁;未配置返回 undefined(不渲染标签)。 */
export function gatePassed(
  summary: Record<string, unknown> | null | undefined,
): boolean | undefined {
  if (summary === null || summary === undefined) return undefined
  if (!Object.prototype.hasOwnProperty.call(summary, 'gate_passed')) return undefined
  return summary.gate_passed === true
}

/** 附件名优先取服务端 Content-Disposition,缺失时回退 `run-{id}.xml`。 */
export function junitFileName(disposition: string | null, runId: string): string {
  const match = disposition?.match(/filename="?([^";]+)"?/i)
  return match?.[1] ?? `run-${runId}.xml`
}

/** 文本转 Blob 触发浏览器下载(导出端点是 application/xml 文本响应)。 */
export function downloadText(
  content: string,
  filename: string,
  type = 'application/xml;charset=utf-8',
): void {
  const url = URL.createObjectURL(new Blob([content], { type }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}
