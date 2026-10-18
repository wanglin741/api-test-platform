import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'

import ElMessageBox from 'element-plus/es/components/message-box/index'

import type { ApiClient } from '@/api/client'
import { useFolderStore } from '@/stores/folder'

import FolderTree from './FolderTree.vue'

const {
  post,
  patch,
  delete: deleteFn,
} = vi.hoisted(() => ({
  post: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
}))

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return {
    ...actual,
    api: {
      GET: vi.fn(async () => okResponse([])),
      POST: post,
      PATCH: patch,
      DELETE: deleteFn,
    } as unknown as ApiClient,
  }
})

function okResponse(body: unknown) {
  return { data: body, error: undefined, response: new Response() }
}

function folderOut(id: number, name: string, parentId: number | null, sortOrder = 0) {
  return {
    id,
    project_id: 1,
    scope: 'case',
    parent_id: parentId,
    name,
    sort_order: sortOrder,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }
}

// 登录链路 / 下单链路 两根 + 登录下挂子目录
const FOLDERS = [
  folderOut(1, '登录链路', null, 0),
  folderOut(2, '下单链路', null, 1),
  folderOut(3, '短信验证', 1, 0),
]

async function mountTree(selected: number | null = null) {
  setActivePinia(createPinia())
  const store = useFolderStore()
  store.projectId = 1
  store.folders = FOLDERS
  const wrapper = mount(FolderTree, {
    props: { selected },
    global: { plugins: [ElementPlus] },
  })
  await flushPromises()
  return wrapper
}

describe('FolderTree', () => {
  it('渲染:全部用例行 + 未分类虚拟节点 + 目录层级', async () => {
    const wrapper = await mountTree()

    const text = wrapper.text()
    expect(text).toContain('全部用例')
    expect(text).toContain('未分类')
    expect(text).toContain('登录链路')
    expect(text).toContain('下单链路')
    expect(text).toContain('短信验证')
  })

  it('点击节点抛 select:目录抛真实 id,未分类抛 0,全部用例抛 null', async () => {
    const wrapper = await mountTree()

    const rows = wrapper.findAll('.node-row')
    await rows[0]?.trigger('click') // 未分类
    expect(wrapper.emitted('select')?.at(-1)).toEqual([0])

    await rows[1]?.trigger('click') // 登录链路
    expect(wrapper.emitted('select')?.at(-1)).toEqual([1])

    await wrapper.find('.all-row').trigger('click')
    expect(wrapper.emitted('select')?.at(-1)).toEqual([null])
  })

  it('未分类虚拟节点没有操作按钮;普通目录有新建子目录/改名/删除', async () => {
    const wrapper = await mountTree()

    const rows = wrapper.findAll('.node-row')
    expect(rows[0]?.findAll('button')).toHaveLength(0) // 未分类
    expect(rows[1]?.findAll('button')).toHaveLength(3) // 登录链路
  })

  it('搜索过滤:命中的节点保留,未命中的隐藏(未分类常显)', async () => {
    const wrapper = await mountTree()

    await wrapper.find('.tree-search input').setValue('登录')
    await flushPromises()

    const hidden = wrapper
      .findAll('.el-tree-node')
      .filter((n) => n.attributes('style')?.includes('display: none'))
    const hiddenNames = hidden.map((n) => n.text())
    expect(hiddenNames.join()).toContain('下单链路')
    expect(wrapper.text()).toContain('未分类')
    expect(wrapper.text()).toContain('登录链路')
  })

  it('新建子目录:点 + 弹窗,提交带 parent_id', async () => {
    post.mockResolvedValue(okResponse(folderOut(9, '新目录', 1)))
    const wrapper = await mountTree()

    await wrapper.findAll('.node-row')[1]?.findAll('button')[0]?.trigger('click')
    await wrapper.find('.folder-dialog input').setValue('新目录')
    await wrapper.find('.folder-dialog .el-button--primary').trigger('click')
    await flushPromises()

    const [, opts] = post.mock.calls[0] as [string, { body: { name: string; parent_id: number } }]
    expect(opts.body.name).toBe('新目录')
    expect(opts.body.parent_id).toBe(1)
  })

  it('重命名:弹窗回填原名,提交走 PATCH', async () => {
    patch.mockResolvedValue(okResponse(folderOut(1, '登录链路改', null)))
    const wrapper = await mountTree()

    await wrapper.findAll('.node-row')[1]?.findAll('button')[1]?.trigger('click')
    expect((wrapper.find('.folder-dialog input').element as HTMLInputElement).value).toBe(
      '登录链路',
    )
    await wrapper.find('.folder-dialog input').setValue('登录链路改')
    await wrapper.find('.folder-dialog .el-button--primary').trigger('click')
    await flushPromises()

    const [path, opts] = patch.mock.calls[0] as [
      string,
      { params: { path: { folder_id: number } }; body: { name: string } },
    ]
    expect(path).toBe('/api/v1/folders/{folder_id}')
    expect(opts.params.path.folder_id).toBe(1)
    expect(opts.body).toEqual({ name: '登录链路改' })
  })

  it('删除:确认后调 DELETE;删掉的是选中目录则回到全部', async () => {
    // MessageBoxData 是窄联合,字符串面量直接 mock 会撞类型,借 never 旁路
    const confirmSpy = vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm' as never)
    deleteFn.mockResolvedValue({ data: undefined })
    const wrapper = await mountTree(3) // 当前选中「短信验证」

    await wrapper.findAll('.node-row')[2]?.findAll('button')[2]?.trigger('click') // 短信验证(id 3)
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalled()
    expect(deleteFn).toHaveBeenCalledWith(
      '/api/v1/folders/{folder_id}',
      expect.objectContaining({ params: { path: { folder_id: 3 } } }),
    )
    expect(wrapper.emitted('select')?.at(-1)).toEqual([null])
    confirmSpy.mockRestore()
  })

  it('删除被取消:不调 DELETE', async () => {
    const confirmSpy = vi.spyOn(ElMessageBox, 'confirm').mockRejectedValue('cancel')
    const wrapper = await mountTree()

    await wrapper.findAll('.node-row')[1]?.findAll('button')[2]?.trigger('click')
    await flushPromises()

    expect(deleteFn).not.toHaveBeenCalled()
    confirmSpy.mockRestore()
  })
})
