/**
 * DSL 前端类型与工具(与后端 app/engine/dsl.py 对齐)。
 * 生成的 schema.d.ts 里 dsl 是宽泛的 Record<string, unknown>,表单编辑需要精确类型。
 * 校验仍以后端 parse_dsl 为准:这里只做「足够表单安全地编辑」的最小结构化。
 */

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' | 'HEAD' | 'OPTIONS'
export type BodyType = 'json' | 'raw' | 'form-data' | 'urlencoded'
export type ExtractType = 'jsonpath' | 'header' | 'status_code'
export type AssertType = 'status_code' | 'jsonpath' | 'response_time_ms'
export type AssertOperator = 'eq' | 'ne' | 'gt' | 'gte' | 'lt' | 'lte' | 'contains' | 'regex_match'

export const HTTP_METHODS: HttpMethod[] = [
  'GET',
  'POST',
  'PUT',
  'PATCH',
  'DELETE',
  'HEAD',
  'OPTIONS',
]
export const BODY_TYPES: BodyType[] = ['json', 'raw', 'form-data', 'urlencoded']
export const EXTRACT_TYPES: ExtractType[] = ['jsonpath', 'header', 'status_code']
export const ASSERT_TYPES: AssertType[] = ['status_code', 'jsonpath', 'response_time_ms']
export const ASSERT_OPERATORS: AssertOperator[] = [
  'eq',
  'ne',
  'gt',
  'gte',
  'lt',
  'lte',
  'contains',
  'regex_match',
]

export interface RequestBody {
  type: BodyType
  content?: unknown
}

export interface RequestSpec {
  method: HttpMethod
  url: string
  // 表单编辑需要稳定存在(normalizeDsl/newStep 保证);后端语义不变,缺省等价 {}
  headers: Record<string, string>
  params: Record<string, string>
  body?: RequestBody | null
  timeout_ms?: number | null
  follow_redirects?: boolean
  verify_ssl?: boolean
}

export interface ExtractSpec {
  name: string
  type: ExtractType
  expression?: string | null
  required?: boolean
}

export interface AssertSpec {
  type: AssertType
  operator: AssertOperator
  expected?: unknown
  expression?: string | null
  message?: string | null
}

export interface RetrySpec {
  times?: number
  interval_ms?: number
  backoff?: 'fixed' | 'exponential'
}

export interface StepSpec {
  id: string
  name: string
  type: 'http'
  enabled?: boolean
  request: RequestSpec
  extract?: ExtractSpec[]
  assert?: AssertSpec[]
  retry: RetrySpec
}

export interface ConfigSpec {
  variables: Record<string, unknown>
  timeout_ms?: number
  fail_fast?: boolean
  max_duration_ms?: number | null
}

export interface CaseDsl {
  dsl_version: number
  /** normalizeDsl 保证存在(缺省补 {variables:{},timeout_ms:10000,fail_fast:false}) */
  config: ConfigSpec
  steps: StepSpec[]
}

/** KV 行(KV 编辑器的中间形态:dict ↔ 行数组) */
export interface KvRow {
  key: string
  value: string
}

export function toKvRows(record: Record<string, unknown> | undefined): KvRow[] {
  return Object.entries(record ?? {}).map(([key, value]) => ({ key, value: String(value) }))
}

/** 空键行丢弃(空行是「刚点了添加还没填」的合法状态) */
export function fromKvRows(rows: KvRow[]): Record<string, string> {
  const result: Record<string, string> = {}
  for (const row of rows) {
    if (row.key.trim() !== '') result[row.key.trim()] = row.value
  }
  return result
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function asStringDict(value: unknown): Record<string, string> {
  const raw = asRecord(value)
  const result: Record<string, string> = {}
  for (const [k, v] of Object.entries(raw)) result[k] = String(v)
  return result
}

const KV_BODY_TYPES: BodyType[] = ['form-data', 'urlencoded']

/** 请求体规范化:KV 型 body 的 content 保证是 dict;json/raw 的 content 原样保留 */
function normalizeBody(raw: unknown): RequestBody {
  const body = asRecord(raw)
  const type = asString(body.type, 'json') as BodyType
  const content = 'content' in body ? body.content : null
  if (KV_BODY_TYPES.includes(type)) {
    return { ...body, type, content: asRecord(content) }
  }
  return { ...body, type, content }
}

/**
 * 深度规范化为表单可编辑的 CaseDsl:缺省字段补默认值。
 * 已知字段之外的原样保留(spread 优先,再覆写已知字段),保存时不丢用户手写的扩展字段。
 */
export function normalizeDsl(raw: unknown): CaseDsl {
  const root = asRecord(raw)
  const configRaw = asRecord(root.config)
  const stepsRaw = Array.isArray(root.steps) ? root.steps : []
  return {
    ...root,
    dsl_version: typeof root.dsl_version === 'number' ? root.dsl_version : 1,
    config: {
      ...configRaw,
      variables: asRecord(configRaw.variables),
      timeout_ms: typeof configRaw.timeout_ms === 'number' ? configRaw.timeout_ms : 10_000,
      fail_fast: configRaw.fail_fast === true,
    },
    steps: stepsRaw.map((step) => {
      const s = asRecord(step)
      const request = asRecord(s.request)
      const retry = asRecord(s.retry)
      return {
        ...s,
        id: asString(s.id),
        name: asString(s.name),
        type: 'http' as const,
        enabled: s.enabled !== false,
        request: {
          ...request,
          method: asString(request.method, 'GET') as HttpMethod,
          url: asString(request.url),
          headers: asStringDict(request.headers),
          params: asStringDict(request.params),
          body:
            request.body === null || request.body === undefined
              ? null
              : normalizeBody(request.body),
          timeout_ms: typeof request.timeout_ms === 'number' ? request.timeout_ms : null,
          follow_redirects: request.follow_redirects !== false,
          verify_ssl: request.verify_ssl !== false,
        },
        extract: (Array.isArray(s.extract) ? s.extract : []).map((e) => {
          const spec = asRecord(e)
          return {
            ...spec,
            name: asString(spec.name),
            type: asString(spec.type, 'jsonpath') as ExtractType,
            expression: asString(spec.expression),
            required: spec.required !== false,
          }
        }),
        assert: (Array.isArray(s.assert) ? s.assert : []).map((a) => {
          const spec = asRecord(a)
          return {
            ...spec,
            type: asString(spec.type, 'status_code') as AssertType,
            operator: asString(spec.operator, 'eq') as AssertOperator,
            expression: asString(spec.expression),
            message: asString(spec.message),
          }
        }),
        retry: {
          ...retry,
          times: typeof retry.times === 'number' ? retry.times : 0,
          interval_ms: typeof retry.interval_ms === 'number' ? retry.interval_ms : 1000,
          backoff: retry.backoff === 'exponential' ? ('exponential' as const) : ('fixed' as const),
        },
      }
    }) as StepSpec[],
  } as CaseDsl
}

export function toDslText(dsl: CaseDsl): string {
  return JSON.stringify(dsl, null, 2)
}

export type ParseDslResult = { ok: true; dsl: CaseDsl } | { ok: false; error: string }

/** 文本 → dsl。只做 JSON 与「根对象、steps 是数组」检查,深校验交给后端。 */
export function parseDslText(text: string): ParseDslResult {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch (error) {
    return {
      ok: false,
      error: `JSON 语法错误:${error instanceof Error ? error.message : String(error)}`,
    }
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return { ok: false, error: 'DSL 必须是一个 JSON 对象' }
  }
  const steps = asRecord(parsed).steps
  if (!Array.isArray(steps)) {
    return { ok: false, error: 'DSL 缺少 steps 数组' }
  }
  return { ok: true, dsl: normalizeDsl(parsed) }
}

export function newStep(existing: StepSpec[]): StepSpec {
  return {
    id: nextStepId(existing),
    name: `步骤 ${existing.length + 1}`,
    type: 'http',
    enabled: true,
    request: { method: 'GET', url: '', headers: {}, params: {}, body: null },
    extract: [],
    assert: [],
    retry: { times: 0, interval_ms: 1000, backoff: 'fixed' },
  }
}

/** 生成不与现有步骤冲突的 id:step-1 起,被占用就顺延 */
export function nextStepId(existing: StepSpec[]): string {
  const used = new Set(existing.map((s) => s.id))
  let n = 1
  while (used.has(`step-${n}`)) n += 1
  return `step-${n}`
}
