from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Text, ForeignKey, Integer, DateTime, func, Table, Column, BigInteger, Boolean
from sqlalchemy.dialects import postgresql
from app.db import Base

class Tag(Base):
    __tablename__ = "tags"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)

# Таблица связи многие-ко-многим для тегов артефактов
artifact_tags = Table(
    "artifact_tags",
    Base.metadata,
    Column("artifact_id", ForeignKey("artifacts.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_name", ForeignKey("tags.name", ondelete="CASCADE"), primary_key=True),
)

class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="project", cascade="all, delete-orphan")

class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))  # note|import
    title: Mapped[str] = mapped_column(String(256))
    raw_text: Mapped[str] = mapped_column(Text)
    uri: Mapped[str | None] = mapped_column(String(512), nullable=True)  # MinIO URL or other storage reference
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("artifacts.id", ondelete="SET NULL"))
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped[Project] = relationship(back_populates="artifacts")
    chunks: Mapped[list["Chunk"]] = relationship(back_populates="artifact", cascade="all, delete-orphan")
    # Теги как список Tag объектов (через association table)
    tags: Mapped[list["Tag"]] = relationship("Tag", secondary=artifact_tags, lazy="selectin")

class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[int] = mapped_column(primary_key=True)
    artifact_id: Mapped[int] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"))
    idx: Mapped[int] = mapped_column(Integer)  # порядковый номер чанка
    text: Mapped[str] = mapped_column(Text)
    tokens: Mapped[int] = mapped_column(Integer)

    artifact: Mapped[Artifact] = relationship(back_populates="chunks")

class BotMessage(Base):
    __tablename__ = "bot_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    tg_message_id: Mapped[int] = mapped_column(Integer)
    reply_to_user_msg_id: Mapped[int | None] = mapped_column(Integer)
    artifact_id: Mapped[int | None] = mapped_column(ForeignKey("artifacts.id", ondelete="SET NULL"))
    saved: Mapped[bool] = mapped_column(Boolean, default=False)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True)
    used_projects: Mapped[dict | None] = mapped_column(postgresql.JSONB, nullable=True)  # {"ids":[...]}
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

user_linked_projects = Table(
    "user_linked_projects", Base.metadata,
    Column("user_id", BigInteger, primary_key=True),
    Column("project_id", ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
)

class UserState(Base):
    __tablename__ = "user_state"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    active_project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    preferred_model: Mapped[str] = mapped_column(String(32), default="gpt-5")
    chat_mode: Mapped[bool] = mapped_column(Boolean, default=False)  # Chat ON/OFF
    quiet_mode: Mapped[bool] = mapped_column(Boolean, default=False) # Quiet ON/OFF
    sources_mode: Mapped[str] = mapped_column(String(16), default="active")  # active|linked|all|global
    scope_mode: Mapped[str] = mapped_column(String(16), default="auto")      # auto|project|global
    context_kinds: Mapped[str | None] = mapped_column(String(256))
    context_tags: Mapped[str | None] = mapped_column(String(256))
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_panel_msg_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_doc_file_id: Mapped[str | None] = mapped_column(String(128))
    last_doc_name: Mapped[str | None] = mapped_column(String(256))
    last_doc_mime: Mapped[str | None] = mapped_column(String(64))
    last_doc_uploaded_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    # Batch operations fields
    last_batch_ids: Mapped[str | None] = mapped_column(String(1024))
    last_batch_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
    # Selection basket fields
    selected_artifact_ids: Mapped[str | None] = mapped_column(Text)
    auto_clear_selection: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    last_batch_tag: Mapped[str | None] = mapped_column(String(16))
    ask_armed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    awaiting_ask_search: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Memory panel pagination fields
    memory_page_msg_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    memory_footer_msg_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ASK panel pagination fields
    ask_page_msg_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    ask_footer_msg_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

class Repo(Base):
    __tablename__ = "repos"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    alias: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(String(512))
    branch: Mapped[str] = mapped_column(String(64), default="main")
    last_synced_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True))
