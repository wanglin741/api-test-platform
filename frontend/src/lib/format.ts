/**
 * 报告列表/最近执行共用的通过率文案,如 "2/3"。
 * 键名与 engine_tasks 落库一致(total,非 total_steps);summary 未落
 * (运行中)显示 "-"。跳过的步骤不参与通过率(执行引擎 §3),这里
 * 沿用列表口径:passed/total。
 */
export function passRateText(
  summary: { total?: number; passed?: number } | null | undefined,
): string {
  if (!summary || typeof summary.total !== 'number') return '-'
  const passed = typeof summary.passed === 'number' ? summary.passed : 0
  return `${passed}/${summary.total}`
}
