import { describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import type { ApiClient } from '@/api/client'

import { useApiTokenStore } from './apiToken'

function mockClient(handlers: Record<string, unknown> = {}): ApiClient {
  return handlers as unknown as ApiClient
}

function okResponse(body: unknown) {
  return { data: body, error: undefined, response: new Response() }
}

const TOKEN_A = {
  id: 1,
  project_id: 1,
  name: 'Jenkins',
  scopes: ['run:trigger', 'report:read'],
  expires_at: '2026-12-31T00:00:00Z',
  last_used_at: null,
  created_at: '2026-01-01T00:00:00Z',
}
const TOKEN_B = { ...TOKEN_A, id: 2, name: 'GitLab' }

describe('apiTokenStore(#42)', () => {
  it('load:按项目拉 Token 列表;列表项不含明文', async () => {
    setActivePinia(createPinia())
    const client = mockClient({ GET: vi.fn(async () => okResponse([TOKEN_A])) })
    const store = useApiTokenStore()

    await store.load(1, client)

    expect(store.items).toHaveLength(1)
    expect(store.items[0]?.name).toBe('Jenkins')
    expect(store.items[0]).not.toHaveProperty('token')
    const [path, opts] = (client.GET as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      { params: { path: { project_id: number } } },
    ]
    expect(path).toBe('/api/v1/projects/{project_id}/api-tokens')
    expect(opts.params.path.project_id).toBe(1)
  })

  it('create:POST 返回一次性明文,但明文只回传给调用方,不落进列表状态', async () => {
    setActivePinia(createPinia())
    const created = { ...TOKEN_A, token: 'at_plaintext' }
    const client = mockClient({
      GET: vi.fn(async () => okResponse([TOKEN_A])),
      POST: vi.fn(async () => okResponse(created)),
    })
    const store = useApiTokenStore()

    const result = await store.create(
      1,
      { name: 'Jenkins', scopes: ['run:trigger', 'report:read'], expires_at: null },
      client,
    )

    expect(result?.token).toBe('at_plaintext')
    expect(store.items).toHaveLength(1)
    expect(store.items[0]).not.toHaveProperty('token')
    const [path, opts] = (client.POST as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      { body: { name: string; scopes: string[] } },
    ]
    expect(path).toBe('/api/v1/projects/{project_id}/api-tokens')
    expect(opts.body.name).toBe('Jenkins')
    expect(opts.body.scopes).toEqual(['run:trigger', 'report:read'])
  })

  it('revoke:DELETE 后重载列表,撤销的 Token 不再出现', async () => {
    setActivePinia(createPinia())
    let tokens: unknown[] = [TOKEN_A, TOKEN_B]
    const client = mockClient({
      GET: vi.fn(async () => okResponse(tokens)),
      DELETE: vi.fn(async () => {
        tokens = [TOKEN_B]
        return okResponse(undefined)
      }),
    })
    const store = useApiTokenStore()
    await store.load(1, client)

    await store.revoke(1, 1, client)

    expect(store.items).toHaveLength(1)
    expect(store.items[0]?.id).toBe(2)
    expect((client.DELETE as ReturnType<typeof vi.fn>).mock.calls[0]).toEqual([
      '/api/v1/api-tokens/{token_id}',
      { params: { path: { token_id: 1 } } },
    ])
  })

  it('代际守卫:慢的旧 load 响应不覆盖新的', async () => {
    setActivePinia(createPinia())
    let releaseFirst: (() => void) | undefined
    const firstGate = new Promise<void>((resolve) => (releaseFirst = resolve))
    let call = 0
    const client = mockClient({
      GET: vi.fn(async () => {
        call += 1
        if (call === 1) {
          await firstGate
          return okResponse([TOKEN_A])
        }
        return okResponse([])
      }),
    })
    const store = useApiTokenStore()

    const stale = store.load(1, client)
    await store.load(1, client)
    releaseFirst?.()
    await stale

    expect(store.items).toHaveLength(0)
  })

  it('reset:清空列表', () => {
    setActivePinia(createPinia())
    const store = useApiTokenStore()
    store.items = [TOKEN_A]

    store.reset()

    expect(store.items).toHaveLength(0)
  })
})
