"""目录 CRUD + move(issue #20,数据模型 §4,ADR-0001)。

树深度限 3;move 做环检测 / 深度校验 / 跨项目拒绝(归属不符一律 404);
删除非空目录(有子目录或有用例)返回 409 而非级联。
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.v1.common import get_project_or_404
from app.core.errors import ConflictError, not_found
from app.models import Folder, Project, TestCase, User
from app.schemas.folders import FolderCreate, FolderMove, FolderOut, FolderUpdate, SCOPE_CASE

MAX_DEPTH = 3

router = APIRouter(prefix="/folders", tags=["folders"])
project_router = APIRouter(prefix="/projects/{project_id}/folders", tags=["folders"])


async def _get_folder_or_404(session: AsyncSession, folder_id: int) -> Folder:
    result = await session.execute(
        select(Folder, Project)
        .join(Project, Folder.project_id == Project.id)
        .where(Folder.id == folder_id, Folder.alive())
    )
    row = result.first()
    if row is None:
        raise not_found("folder", folder_id)
    folder, project = row
    if project.deleted_at is not None:
        raise not_found("folder", folder_id)  # 项目已删,归属资源一律 404
    return folder


def _depths(folders: list[Folder]) -> dict[int, int]:
    """按 id → 深度(顶层为 1)。传入集合内的 parent_id 悬挂时按顶层处理,不炸。"""
    by_id = {f.id: f for f in folders}
    depths: dict[int, int] = {}

    def depth_of(folder: Folder, seen: set[int]) -> int:
        if folder.id in depths:
            return depths[folder.id]
        if folder.parent_id is None or folder.parent_id not in by_id or folder.id in seen:
            depths[folder.id] = 1
        else:
            depths[folder.id] = depth_of(by_id[folder.parent_id], seen | {folder.id}) + 1
        return depths[folder.id]

    for folder in folders:
        depth_of(folder, set())
    return depths


def _subtree_ids(folders: list[Folder], root_id: int) -> set[int]:
    children: dict[int | None, list[int]] = {}
    for folder in folders:
        children.setdefault(folder.parent_id, []).append(folder.id)
    ids: set[int] = set()
    stack = [root_id]
    while stack:
        current = stack.pop()
        ids.add(current)
        stack.extend(children.get(current, []))
    return ids


async def _alive_folders(
    session: AsyncSession, project_id: int, scope: str = SCOPE_CASE
) -> list[Folder]:
    result = await session.execute(
        select(Folder)
        .where(
            Folder.project_id == project_id,
            Folder.scope == scope,
            Folder.alive(),
        )
        .order_by(Folder.sort_order, Folder.id)
    )
    return list(result.scalars())


async def _reject_name_conflict(
    session: AsyncSession,
    *,
    project_id: int,
    scope: str,
    parent_id: int | None,
    name: str,
    exclude_id: int | None = None,
) -> None:
    """同级同名 → 409。

    parent_id 非空时表级 UNIQUE(含软删行)会真的拦 insert,预检须同样查软删行,
    否则冲突以 500 而非 409 暴露;parent_id 为空时 PG 视 NULL 互异,数据库不拦,
    仅拒绝在用重名(软删的顶层名字允许复用)。
    """
    conditions = [
        Folder.project_id == project_id,
        Folder.scope == scope,
        Folder.name == name,
        Folder.parent_id.is_(None) if parent_id is None else Folder.parent_id == parent_id,
    ]
    if parent_id is None:
        conditions.append(Folder.alive())
    if exclude_id is not None:
        conditions.append(Folder.id != exclude_id)
    conflict = await session.execute(select(Folder.id).where(*conditions))
    if conflict.scalar_one_or_none() is not None:
        raise ConflictError(
            code="folder_name_conflict",
            message=f"同级目录下已存在同名目录 {name!r}",
            details={"project_id": project_id, "parent_id": parent_id, "name": name},
        )


async def _reject_bad_parent(
    session: AsyncSession, project_id: int, parent_id: int | None
) -> Folder | None:
    if parent_id is None:
        return None
    parent = await session.get(Folder, parent_id)
    # 不存在 / 已删 / 跨项目一律 404(同调试端点的归属语义,不做探测)
    if parent is None or parent.deleted_at is not None or parent.project_id != project_id:
        raise not_found("folder", parent_id)
    return parent


@project_router.post("", status_code=201, response_model=FolderOut)
async def create_folder(
    project_id: int,
    payload: FolderCreate,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Folder:
    await get_project_or_404(session, project_id)
    parent = await _reject_bad_parent(session, project_id, payload.parent_id)
    if parent is not None:
        depths = _depths(await _alive_folders(session, project_id, payload.scope))
        if depths.get(parent.id, 1) + 1 > MAX_DEPTH:
            raise ConflictError(
                code="folder_depth_exceeded",
                message=f"目录层级最深 {MAX_DEPTH} 级",
                details={"parent_id": payload.parent_id, "max_depth": MAX_DEPTH},
            )
    await _reject_name_conflict(
        session,
        project_id=project_id,
        scope=payload.scope,
        parent_id=payload.parent_id,
        name=payload.name,
    )
    folder = Folder(project_id=project_id, **payload.model_dump())
    session.add(folder)
    await session.flush()
    await session.refresh(folder)  # 取回 server_default 列
    return folder


@project_router.get("", response_model=list[FolderOut])
async def list_folders(
    project_id: int,
    scope: str = Query(default=SCOPE_CASE, max_length=16),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Folder]:
    await get_project_or_404(session, project_id)
    return await _alive_folders(session, project_id, scope)


@router.patch("/{folder_id}", response_model=FolderOut)
async def rename_folder(
    folder_id: int,
    payload: FolderUpdate,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Folder:
    folder = await _get_folder_or_404(session, folder_id)
    if payload.name is not None and payload.name != folder.name:
        await _reject_name_conflict(
            session,
            project_id=folder.project_id,
            scope=folder.scope,
            parent_id=folder.parent_id,
            name=payload.name,
            exclude_id=folder.id,
        )
        folder.name = payload.name
    await session.flush()
    await session.refresh(folder)
    return folder


@router.post("/{folder_id}/move", response_model=FolderOut)
async def move_folder(
    folder_id: int,
    payload: FolderMove,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Folder:
    folder = await _get_folder_or_404(session, folder_id)
    parent = await _reject_bad_parent(session, folder.project_id, payload.parent_id)

    siblings = await _alive_folders(session, folder.project_id, folder.scope)
    if parent is not None:
        # 环检测:不可移动到自身或其后代
        if parent.id in _subtree_ids(siblings, folder.id):
            raise ConflictError(
                code="folder_cycle",
                message="不能把目录移动到它自己或它的子目录下",
                details={"folder_id": folder.id, "parent_id": payload.parent_id},
            )
        # 深度校验:新父深度 + 被移子树高度 ≤ 3
        depths = _depths(siblings)
        subtree_ids = _subtree_ids(siblings, folder.id)
        subtree = [f for f in siblings if f.id in subtree_ids]
        height = max(_depths(subtree).values(), default=1)
        if depths.get(parent.id, 1) + height > MAX_DEPTH:
            raise ConflictError(
                code="folder_depth_exceeded",
                message=f"目录层级最深 {MAX_DEPTH} 级",
                details={"folder_id": folder.id, "parent_id": payload.parent_id},
            )

    folder.parent_id = payload.parent_id
    folder.sort_order = payload.sort_order
    await session.flush()
    await session.refresh(folder)
    return folder


@router.delete("/{folder_id}", status_code=204)
async def delete_folder(
    folder_id: int,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    folder = await _get_folder_or_404(session, folder_id)
    child = await session.scalar(
        select(func.count())
        .select_from(Folder)
        .where(Folder.parent_id == folder.id, Folder.alive())
    )
    if child:
        raise ConflictError(
            code="folder_not_empty",
            message="目录下还有子目录,先清空再删除",
            details={"folder_id": folder.id},
        )
    used = await session.scalar(
        select(func.count())
        .select_from(TestCase)
        .where(TestCase.folder_id == folder.id, TestCase.alive())
    )
    if used:
        raise ConflictError(
            code="folder_not_empty",
            message="目录下还有用例,先移走再删除",
            details={"folder_id": folder.id},
        )
    folder.deleted_at = func.now()
    await session.flush()
