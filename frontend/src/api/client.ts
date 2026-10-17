/**
 * API client 工厂:openapi-fetch + OpenAPI 生成的类型直通(前端设计 §3、§8)。
 *
 * 错误约定:后端非 2xx 恒为 {"error":{code,message,details}} —— 中间件统一解析,
 * 经 onError 通知(默认 ElMessage)后抛 ApiError,store 捕获或放行。
 * onError 可注入,store 缝测试用它换成 spy。
 */

import ElMessage from 'element-plus/es/components/message/index'
import createClient from 'openapi-fetch'

import type { components, paths } from './schema'

export type Project = components['schemas']['ProjectOut']
export type ApiClient = ReturnType<typeof createClient<paths>>

export class ApiError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly status: number,
    readonly details?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export type ErrorNotifier = (message: string, code: string) => void

type ClientOptions = Parameters<typeof createClient<paths>>[0]

export function createApiClient(
  options: { onError?: ErrorNotifier } & Partial<ClientOptions> = {},
): ApiClient {
  const { onError, ...clientOptions } = options
  // openapi.json 的 paths 自带 /api/v1 前缀:浏览器里 baseUrl 留空走同源相对路径
  // (dev 由 Vite 代理);测试经 fetch 选项注入 mock,不碰全局
  const client = createClient<paths>(clientOptions)
  client.use({
    async onResponse({ response }) {
      if (response.ok) return undefined
      let message = `请求失败(${response.status})`
      let code = 'unknown_error'
      let details: unknown
      try {
        const body = (await response.clone().json()) as {
          error?: { code?: string; message?: string; details?: unknown }
        }
        if (body.error) {
          code = body.error.code ?? code
          message = body.error.message ?? message
          details = body.error.details
        }
      } catch {
        // 非 JSON 错误体,保持默认文案
      }
      onError?.(message, code)
      throw new ApiError(message, code, response.status, details)
    },
  })
  return client
}

export const api: ApiClient = createApiClient({
  onError: (message, code) => {
    ElMessage.error(import.meta.env.DEV ? `${message}（${code}）` : message)
  },
})
