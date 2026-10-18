import { describe, expect, it } from 'vitest'

import { normalizeDsl, parseDslText } from './dsl'

describe('normalizeDsl', () => {
  it('手写 DSL 用后端字段名 assertions:收编成 assert,断言不丢', () => {
    const dsl = normalizeDsl({
      dsl_version: 1,
      steps: [
        {
          id: 's1',
          name: 's1',
          type: 'http',
          request: { method: 'GET', url: 'https://api.example.com/x' },
          assertions: [{ type: 'status_code', operator: 'eq', expected: 200 }],
        },
      ],
    })

    const step = dsl.steps[0]
    expect(step?.assert).toHaveLength(1)
    expect(step?.assert?.[0]?.expected).toBe(200)
    // spread 残留的 assertions 键必须删掉:两个键并存时后端别名优先,断言会被空数组覆盖
    expect((step as unknown as Record<string, unknown>)['assertions']).toBeUndefined()
  })

  it('显式 assert 与 assertions 并存:assert 优先', () => {
    const dsl = normalizeDsl({
      dsl_version: 1,
      steps: [
        {
          id: 's1',
          name: 's1',
          type: 'http',
          request: { method: 'GET', url: 'https://api.example.com/x' },
          assert: [{ type: 'status_code', operator: 'eq', expected: 200 }],
          assertions: [{ type: 'status_code', operator: 'eq', expected: 418 }],
        },
      ],
    })

    expect(dsl.steps[0]?.assert?.[0]?.expected).toBe(200)
  })

  it('缺失的表达式保持 null,不 manufactured 成空串', () => {
    const dsl = normalizeDsl({
      dsl_version: 1,
      steps: [
        {
          id: 's1',
          name: 's1',
          type: 'http',
          request: { method: 'GET', url: 'https://api.example.com/x' },
          extract: [{ name: 'hello', type: 'jsonpath' }],
          assert: [{ type: 'jsonpath', operator: 'eq', expected: 1 }],
        },
      ],
    })

    expect(dsl.steps[0]?.extract?.[0]?.expression).toBeNull()
    expect(dsl.steps[0]?.assert?.[0]?.expression).toBeNull()
  })

  it('已有表达式原样保留;parseDslText 走同一规范化', () => {
    const result = parseDslText(
      JSON.stringify({
        dsl_version: 1,
        steps: [
          {
            id: 's1',
            name: 's1',
            type: 'http',
            request: { method: 'GET', url: 'https://api.example.com/x' },
            extract: [{ name: 'a', type: 'jsonpath', expression: '$.a' }],
          },
        ],
      }),
    )

    expect(result.ok).toBe(true)
    if (result.ok) expect(result.dsl.steps[0]?.extract?.[0]?.expression).toBe('$.a')
  })
})
