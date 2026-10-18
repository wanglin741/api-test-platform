<script setup lang="ts">
import { Plus } from '@element-plus/icons-vue'
import ElMessageBox from 'element-plus/es/components/message-box/index'
import { computed, ref, watch } from 'vue'

import { useFolderStore } from '@/stores/folder'

import { canDrop, MAX_DEPTH, resolveMove, UNCATEGORIZED_ID } from './folderTree'

import type { FolderNode } from '@/stores/folder'
import type { DropType } from './folderTree'
import type { TreeInstance } from 'element-plus'

/** el-tree 节点的结构子集(够用即可,不引完整 Node 类型)。 */
interface ElNode {
  data: FolderNode
  level: number
  parent?: { data: FolderNode | FolderNode[] }
}

/**
 * 目录树(左树右表的「左树」,issue #20)。
 * 数据来自 folderStore;「未分类」是前端虚拟节点(id 0),不可拖拽/改名/删除。
 * 选中目录向上抛 select(全部 = null,未分类 = 0,目录 = 真实 id)。
 */
const emit = defineEmits<{ select: [folderId: number | null] }>()

const props = defineProps<{ selected: number | null }>()

const folderStore = useFolderStore()

const UNCATEGORIZED_NODE: FolderNode = {
  id: UNCATEGORIZED_ID,
  name: '未分类',
  sort_order: 0,
  children: [],
}

const treeData = computed<FolderNode[]>(() => [UNCATEGORIZED_NODE, ...folderStore.tree])

const treeRef = ref<TreeInstance>()

// ── 搜索过滤 ─────────────────────────────────────────────────────
const keyword = ref('')
watch(keyword, (value) => treeRef.value?.filter(value))

// 未分类虚拟节点常显,其余按名过滤
function filterNode(value: string, data: FolderNode): boolean {
  return data.id === UNCATEGORIZED_ID || data.name.includes(value)
}

// ── 新建 / 重命名弹窗 ────────────────────────────────────────────
const dialogVisible = ref(false)
const dialogMode = ref<'create' | 'rename'>('create')
const editingId = ref<number | null>(null)
const createParentId = ref<number | null>(null)
const nameInput = ref('')
const submitting = ref(false)

function openCreate(parentId: number | null) {
  dialogMode.value = 'create'
  createParentId.value = parentId
  nameInput.value = ''
  dialogVisible.value = true
}

function openRename(node: FolderNode) {
  dialogMode.value = 'rename'
  editingId.value = node.id
  nameInput.value = node.name
  dialogVisible.value = true
}

async function submitDialog() {
  const name = nameInput.value.trim()
  if (!name) return
  submitting.value = true
  try {
    if (dialogMode.value === 'create') {
      await folderStore.create({ name, parent_id: createParentId.value })
    } else if (editingId.value !== null) {
      await folderStore.rename(editingId.value, name)
    }
    dialogVisible.value = false
  } catch {
    // 重名/层级超限等已由全局拦截器弹出;弹窗保持打开
  } finally {
    submitting.value = false
  }
}

// ── 删除 ─────────────────────────────────────────────────────────
async function removeNode(node: FolderNode) {
  try {
    await ElMessageBox.confirm(`删除目录「${node.name}」?仅能删除空目录。`, '删除目录', {
      type: 'warning',
    })
  } catch {
    return // 用户取消
  }
  try {
    await folderStore.remove(node.id)
    if (props.selected === node.id) emit('select', null) // 选中的目录没了,回到全部
  } catch {
    // 非空 409 已由全局拦截器弹出
  }
}

// ── 拖拽移动 ─────────────────────────────────────────────────────
function allowDrag(node: ElNode): boolean {
  return node.data.id !== UNCATEGORIZED_ID
}

function allowDrop(dragging: ElNode, drop: ElNode, type: DropType): boolean {
  return canDrop(dragging.data, drop.data.id, drop.level, type)
}

async function onNodeDrop(dragging: ElNode, drop: ElNode, type: DropType) {
  // drop 节点的父:el-tree 根哨兵的 data 是数组,没有 id → 顶层 null
  const rawParent = drop.parent?.data ?? null
  const dropParentId = rawParent !== null && !Array.isArray(rawParent) ? rawParent.id : null
  const { parentId, sortOrder } = resolveMove(dragging.data, drop.data, dropParentId, type)
  try {
    await folderStore.move(dragging.data.id, parentId, sortOrder)
  } catch {
    // move 被拒(环/深度/跨项目)已由全局拦截器弹出;store 重取后树回到服务端形态
  }
}
</script>

<template>
  <div class="folder-tree">
    <div class="tree-head">
      <el-button :icon="Plus" text type="primary" size="small" @click="openCreate(null)">
        根目录
      </el-button>
    </div>
    <el-input v-model="keyword" placeholder="搜索目录" clearable size="small" class="tree-search" />
    <div class="all-row" :class="{ active: props.selected === null }" @click="emit('select', null)">
      全部用例
    </div>
    <el-tree
      ref="treeRef"
      class="tree"
      :data="treeData"
      node-key="id"
      :props="{ label: 'name' }"
      :expand-on-click-node="false"
      default-expand-all
      highlight-current
      :current-node-key="props.selected ?? undefined"
      draggable
      :allow-drag="allowDrag"
      :allow-drop="allowDrop"
      :filter-node-method="filterNode"
      @node-click="(data: FolderNode) => emit('select', data.id)"
      @node-drop="onNodeDrop"
    >
      <template #default="{ data, node }">
        <div class="node-row" :class="{ active: data.id === props.selected }">
          <span class="node-name">{{ data.name }}</span>
          <span class="node-ops" @click.stop>
            <el-button
              v-if="data.id !== UNCATEGORIZED_ID && node.level < MAX_DEPTH"
              :icon="Plus"
              text
              size="small"
              title="新建子目录"
              @click="openCreate(data.id)"
            />
            <el-button
              v-if="data.id !== UNCATEGORIZED_ID"
              text
              size="small"
              title="重命名"
              @click="openRename(data)"
            >
              改名
            </el-button>
            <el-button
              v-if="data.id !== UNCATEGORIZED_ID"
              text
              size="small"
              type="danger"
              title="删除"
              @click="removeNode(data)"
            >
              删
            </el-button>
          </span>
        </div>
      </template>
    </el-tree>

    <el-dialog
      v-model="dialogVisible"
      :title="dialogMode === 'create' ? '新建目录' : '重命名目录'"
      width="360px"
      class="folder-dialog"
    >
      <el-input
        v-model="nameInput"
        maxlength="128"
        placeholder="目录名称"
        @keyup.enter="submitDialog"
      />
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitDialog">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.tree-head {
  display: flex;
  justify-content: flex-end;
}

.tree-search {
  margin-bottom: 8px;
}

.all-row {
  padding: 4px 8px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 14px;
}

.all-row:hover,
.all-row.active {
  background: var(--el-fill-color-light);
  color: var(--el-color-primary);
}

.node-row {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-width: 0;
  padding-right: 4px;
}

.node-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 操作按钮与用例行同理:hover 或选中时现身,否则找不到入口 */
.node-ops {
  display: none;
  flex-shrink: 0;
}

.node-row:hover .node-ops,
.node-row.active .node-ops {
  display: inline-flex;
}

.node-ops .el-button + .el-button {
  margin-left: 0;
}
</style>
