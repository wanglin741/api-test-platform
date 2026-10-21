import { describe, expect, it, vi } from 'vitest'

import { downloadText, gatePassed, junitFileName, triggerModeLabel } from './format'

describe('报告格式化工具(#42)', () => {
  it('trigger_mode 中文映射:四种来源 + 未知值原样回退', () => {
    expect(triggerModeLabel('manual')).toBe('手动')
    expect(triggerModeLabel('schedule')).toBe('定时')
    expect(triggerModeLabel('openapi')).toBe('OpenAPI')
    expect(triggerModeLabel('ci')).toBe('CI')
    expect(triggerModeLabel('future')).toBe('future')
  })

  it('gate_passed:键存在返回布尔,键不存在/无 summary 返回 undefined', () => {
    expect(gatePassed({ gate_passed: true })).toBe(true)
    expect(gatePassed({ gate_passed: false })).toBe(false)
    expect(gatePassed({ total: 1 })).toBeUndefined()
    expect(gatePassed(null)).toBeUndefined()
    expect(gatePassed(undefined)).toBeUndefined()
  })

  it('junitFileName:优先取 Content-Disposition,缺失回退 run-{id}.xml', () => {
    expect(junitFileName('attachment; filename="run-abc.xml"', 'abc')).toBe('run-abc.xml')
    expect(junitFileName('attachment; filename=run-abc.xml', 'abc')).toBe('run-abc.xml')
    expect(junitFileName(null, 'abc')).toBe('run-abc.xml')
  })

  it('downloadText:文本转 Blob 并用文件名触发下载', () => {
    const createObjectURL = vi.fn((_blob: Blob) => 'blob:junit')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    downloadText('<testsuites/>', 'run-1.xml')

    expect(createObjectURL).toHaveBeenCalledOnce()
    const blob = createObjectURL.mock.calls[0]?.[0] as Blob
    expect(blob.type).toContain('xml')
    expect(click).toHaveBeenCalledOnce()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:junit')

    click.mockRestore()
    vi.unstubAllGlobals()
  })
})
