from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Iterable
from app.models import Project, Artifact, Chunk, UserState, Tag, artifact_tags, user_linked_projects

ALLOWED_MODELS = {"gpt-5", "gpt-5-thinking"}
DEFAULT_MODEL = "gpt-5"

async def set_active_project(session: AsyncSession, user_id: int, project: Project):
    st = await session.get(UserState, user_id)
    if not st:
        st = UserState(user_id=user_id, active_project_id=project.id)
        session.add(st)
    else:
        st.active_project_id = project.id
    await session.flush()

async def get_active_project(session: AsyncSession, user_id: int) -> Project | None:
    st = await session.get(UserState, user_id)
    if not st or not st.active_project_id:
        return None
    return await session.get(Project, st.active_project_id)

async def list_artifacts(session: AsyncSession, project: Project, kinds: set[str] | None = None, tags: set[str] | None = None) -> list[Artifact]:
    """List artifacts with optional filtering by kinds and tags."""
    q = select(Artifact).where(Artifact.project_id == project.id)
    
    if kinds:
        q = q.where(Artifact.kind.in_(list(kinds)))
    
    if tags:
        q = q.join(artifact_tags, artifact_tags.c.artifact_id == Artifact.id)\
             .join(Tag, Tag.name == artifact_tags.c.tag_name)\
             .where(Tag.name.in_(list(tags)))
    
    q = q.order_by(Artifact.created_at.desc())
    res = await session.execute(q)
    return list(res.scalars().all())

async def gather_context(session: AsyncSession, project: Project, user_id: int | None = None, max_chunks: int = 200) -> list[str]:
    """Gather context chunks with optional user-specific filtering."""
    # Get user's context filters if user_id provided
    kinds = None
    tags = None
    
    if user_id:
        # For now, use all artifacts - context filtering can be added later
        pass
    
    # Get artifacts (with potential filtering)
    artifacts = await list_artifacts(session, project, kinds=kinds, tags=tags)
    
    context_chunks: list[str] = []
    for art in artifacts:
        res2 = await session.execute(select(Chunk).where(Chunk.artifact_id == art.id).order_by(Chunk.idx.asc()))
        chs = list(res2.scalars().all())
        for c in chs:
            if len(context_chunks) >= max_chunks:
                return context_chunks
            context_chunks.append(c.text)
    return context_chunks

async def set_context_filters(session: AsyncSession, user_id: int, kinds_csv: str = "", tags_csv: str = ""):
    """Set context filtering preferences for a user."""
    st = await session.get(UserState, user_id)
    if not st:
        st = UserState(user_id=user_id, context_kinds=kinds_csv, context_tags=tags_csv)
        session.add(st)
    else:
        if kinds_csv != "":
            st.context_kinds = kinds_csv
        if tags_csv != "":
            st.context_tags = tags_csv
    await session.flush()

async def get_context_filters_state(session: AsyncSession, user_id: int) -> tuple[list[str], list[str]]:
    st = await session.get(UserState, user_id)
    kinds = [s.strip() for s in (st.context_kinds or "").split(",") if s.strip()] if st else []
    tags = [s.strip() for s in (st.context_tags or "").split(",") if s.strip()] if st else []
    return kinds, tags

async def count_artifacts(session: AsyncSession, project: Project) -> int:
    q = select(func.count()).select_from(Artifact).where(Artifact.project_id == project.id)
    res = await session.execute(q)
    return int(res.scalar() or 0)

async def clear_project(session: AsyncSession, project: Project):
    # простая очистка: удаляем артефакты → каскадно уйдут чанки
    res = await session.execute(select(Artifact.id).where(Artifact.project_id == project.id))
    ids = [row[0] for row in res.all()]
    if ids:
        from sqlalchemy import delete
        await session.execute(delete(Artifact).where(Artifact.id.in_(ids)))

async def get_preferred_model(session: AsyncSession, user_id: int) -> str:
    """Get user's preferred model, return default if not set or invalid."""
    st = await session.get(UserState, user_id)
    if not st or not st.preferred_model:
        return DEFAULT_MODEL
    return st.preferred_model if st.preferred_model in ALLOWED_MODELS else DEFAULT_MODEL

async def set_preferred_model(session: AsyncSession, user_id: int, model: str) -> str:
    """Set user's preferred model, normalize invalid values to default."""
    model = model.strip()
    if model not in ALLOWED_MODELS:
        # нормализуем/возвращаем дефолт
        model = DEFAULT_MODEL
    st = await session.get(UserState, user_id)
    if not st:
        st = UserState(user_id=user_id, preferred_model=model)
        session.add(st)
    else:
        st.preferred_model = model
    await session.flush()
    return model

# --- Новое: флаги/моды ---
async def _ensure_user_state(session: AsyncSession, user_id: int) -> UserState:
    st = await session.get(UserState, user_id)
    if not st:
        st = UserState(user_id=user_id)
        session.add(st)
        await session.flush()
    # страховка от NULL в старых записях/миграциях
    if not st.sources_mode:
        st.sources_mode = "active"
    if not st.scope_mode:
        st.scope_mode = "auto"
    if st.chat_mode is None:
        st.chat_mode = False
    if st.quiet_mode is None:
        st.quiet_mode = False
    return st

async def get_chat_flags(session: AsyncSession, user_id: int):
    st = await _ensure_user_state(session, user_id)
    return st.chat_mode, st.quiet_mode, st.sources_mode, st.scope_mode

async def set_chat_mode(session: AsyncSession, user_id: int, on: bool):
    st = await _ensure_user_state(session, user_id)
    st.chat_mode = bool(on)
    await session.flush()
    return st.chat_mode

async def set_quiet_mode(session: AsyncSession, user_id: int, on: bool):
    st = await _ensure_user_state(session, user_id)
    st.quiet_mode = bool(on)
    await session.flush()
    return st.quiet_mode

async def toggle_scope(session: AsyncSession, user_id: int):
    st = await _ensure_user_state(session, user_id)
    order = ["auto", "project", "global"]
    if st.scope_mode not in order:
        st.scope_mode = order[0]
    else:
        st.scope_mode = order[(order.index(st.scope_mode) + 1) % len(order)]
    await session.flush()
    return st.scope_mode

async def toggle_sources(session: AsyncSession, user_id: int):
    st = await _ensure_user_state(session, user_id)
    order = ["active", "linked", "all", "global"]
    if st.sources_mode not in order:
        st.sources_mode = order[0]
    else:
        st.sources_mode = order[(order.index(st.sources_mode) + 1) % len(order)]
    await session.flush()
    return st.sources_mode

# --- Linked projects ---
async def list_projects(session: AsyncSession) -> list[Project]:
    res = await session.execute(select(Project).order_by(Project.id.desc()))
    return list(res.scalars())

async def get_linked_project_ids(session: AsyncSession, user_id: int) -> list[int]:
    res = await session.execute(select(user_linked_projects.c.project_id).where(user_linked_projects.c.user_id == user_id))
    return [r[0] for r in res.fetchall()]

async def link_toggle_project(session: AsyncSession, user_id: int, project_id: int) -> bool:
    # returns new state: True if linked now, False if unlinked
    rows = (await session.execute(
        select(user_linked_projects.c.project_id).where(
            and_(user_linked_projects.c.user_id == user_id,
                 user_linked_projects.c.project_id == project_id)
        )
    )).fetchall()
    if rows:
        await session.execute(user_linked_projects.delete().where(
            and_(user_linked_projects.c.user_id == user_id,
                 user_linked_projects.c.project_id == project_id)
        ))
        return False
    else:
        await session.execute(user_linked_projects.insert().values(user_id=user_id, project_id=project_id))
        return True

# --- Сбор контекста из нескольких источников ---
async def gather_context_sources(
    session: AsyncSession,
    active_project: Project | None,
    user_id: int,
    max_chunks: int,
    kinds: list[str] | None = None,
    tags: list[str] | None = None,
    sources_mode: str = "active",
    linked_ids: list[int] | None = None,
) -> tuple[list[str], list[int]]:
    """
    Возвращает (chunks_texts, used_project_ids)
    NB: заглушка – семантический поиск предполагается в другом модуле;
    здесь – отбор по видам/тегам по проектам и ограничение по max_chunks.
    """
    project_ids: list[int] = []
    if sources_mode == "global":
        return [], []
    if sources_mode == "all":
        res = await session.execute(select(Project.id))
        project_ids = [r[0] for r in res.fetchall()]
    elif sources_mode == "linked":
        project_ids = [active_project.id] if active_project else []
        if linked_ids:
            project_ids += [pid for pid in linked_ids if pid not in project_ids]
    else:  # "active"
        project_ids = [active_project.id] if active_project else []

    if not project_ids:
        return [], []

    # Простой релевант: последние чанки, отфильтрованные по tags/kinds (MVP).
    q = select(Chunk.text, Artifact.project_id).join(Artifact, Chunk.artifact_id == Artifact.id)
    q = q.where(Artifact.project_id.in_(project_ids))
    if kinds:
        q = q.where(Artifact.kind.in_(kinds))
    if tags:
        # у тебя tags как relationship через secondary; для простоты MVP отбросим фильтр по tags здесь
        pass
    q = q.order_by(Artifact.created_at.desc(), Chunk.idx.asc()).limit(max_chunks)
    res = await session.execute(q)
    rows = res.fetchall()
    if not rows:
        return [], project_ids
    texts = [r[0] for r in rows]
    used_pids = list({r[1] for r in rows})
    return texts, used_pids