/**
 * 目录树拖拽的纯函数:深度校验与 drop → move 参数换算。
 * 抽出来是为了能在 jsdom 里直接测,不用模拟 el-tree 的拖拽事件。
 */
import type { FolderNode } from '@/stores/folder'

/** 「未分类」虚拟节点 id:不对应数据库行( issue #20),不可删改、不可挂子目录。 */
export const UNCATEGORIZED_ID = 0

export const MAX_DEPTH = 3

export type DropType = 'inner' | 'before' | 'after'

export function subtreeHeight(node: FolderNode): number {
  if (node.children.length === 0) return 1
  return 1 + Math.max(...node.children.map(subtreeHeight))
}

/**
 * drop 是否放行。dropLevel 是 el-tree 的节点 level(根级为 1);
 * inner = 落为 dropNode 的子节点(新深度 dropLevel+1),before/after = 与其同级(新深度 dropLevel)。
 */
export function canDrop(
  dragging: FolderNode,
  dropId: number,
  dropLevel: number,
  type: DropType,
): boolean {
  if (dropId === UNCATEGORIZED_ID) return type !== 'inner'
  const height = subtreeHeight(dragging)
  const deepest = type === 'inner' ? dropLevel + height : dropLevel + height - 1
  return deepest <= MAX_DEPTH
}

/** node-drop 换算成 move 端点参数。dropParentId 是 drop 节点的父目录 id(顶层为 null)。 */
export function resolveMove(
  dragging: FolderNode,
  drop: FolderNode,
  dropParentId: number | null,
  type: DropType,
): { parentId: number | null; sortOrder: number } {
  if (type === 'inner') {
    const others = drop.children.filter((child) => child.id !== dragging.id)
    const max = others.length > 0 ? Math.max(...others.map((c) => c.sort_order)) : -1
    return { parentId: drop.id, sortOrder: max + 1 }
  }
  return {
    parentId: dropParentId,
    sortOrder: type === 'before' ? drop.sort_order : drop.sort_order + 1,
  }
}
