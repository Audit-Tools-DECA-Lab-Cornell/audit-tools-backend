"""
Shared SQLAlchemy ORM models for the Audit Tools backend.

The shared core is intentionally product-agnostic so the YEE and Playspace
databases can evolve around the same dashboard hierarchy while keeping
product-specific audit logic in separate route/service modules.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import Enum as SAEnum

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base used by all ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class AccountType(str, Enum):
    """High-level access class shared by dummy auth and account modeling."""

    ADMIN = "ADMIN"
    MANAGER = "MANAGER"
    AUDITOR = "AUDITOR"


class AuditStatus(str, Enum):
    """Lifecycle states for a shared audit shell record."""

    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"
    SUBMITTED = "SUBMITTED"


JSONDict = dict[str, object]

# Shared cascade configuration for parent -> child relationships.
CASCADE_DELETE_ORPHAN: str = "all, delete-orphan"


class User(Base):
    """
    Temporary auth scaffold.

    This table intentionally remains separate from the shared account hierarchy so
    a teammate can replace the dummy auth implementation later without blocking the
    dashboard/domain remodel.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[AccountType] = mapped_column(
        SAEnum(AccountType, name="shared_account_type", create_type=False),
        nullable=False,
    )
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class Account(Base):
    """
    Shared tenant/account record.

    A manager account owns projects and manager profiles.
    An auditor account maps to exactly one auditor profile.
    """

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_type: Mapped[AccountType] = mapped_column(
        SAEnum(AccountType, name="shared_account_type", create_type=False),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    manager_profiles: Mapped[list[ManagerProfile]] = relationship(
        back_populates="account",
        cascade=CASCADE_DELETE_ORPHAN,
    )
    projects: Mapped[list[Project]] = relationship(
        back_populates="account",
        cascade=CASCADE_DELETE_ORPHAN,
    )
    auditor_profile: Mapped[AuditorProfile | None] = relationship(
        back_populates="account",
        cascade=CASCADE_DELETE_ORPHAN,
        uselist=False,
    )


class ManagerProfile(Base):
    """Profile record for managers attached to a manager account."""

    __tablename__ = "manager_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    position: Mapped[str | None] = mapped_column(String(200), nullable=True)
    organization: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    account: Mapped[Account] = relationship(back_populates="manager_profiles")


class Project(Base):
    """Shared project model used by both product databases."""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    overview: Mapped[str | None] = mapped_column(Text, nullable=True)
    place_types: Mapped[list[str]] = mapped_column(ARRAY(String(100)), default=list, nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    est_places: Mapped[int | None] = mapped_column(Integer, nullable=True)
    est_auditors: Mapped[int | None] = mapped_column(Integer, nullable=True)
    auditor_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    account: Mapped[Account] = relationship(back_populates="projects")
    places: Mapped[list[Place]] = relationship(
        back_populates="project",
        cascade=CASCADE_DELETE_ORPHAN,
    )
    assignments: Mapped[list[AuditorAssignment]] = relationship(
        back_populates="project",
    )


class Place(Base):
    """Shared place/location model used by both product databases."""

    __tablename__ = "places"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    province: Mapped[str | None] = mapped_column(String(120), nullable=True)
    country: Mapped[str | None] = mapped_column(String(120), nullable=True)
    place_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    est_auditors: Mapped[int | None] = mapped_column(Integer, nullable=True)
    auditor_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    project: Mapped[Project] = relationship(back_populates="places")
    assignments: Mapped[list[AuditorAssignment]] = relationship(
        back_populates="place",
        cascade=CASCADE_DELETE_ORPHAN,
    )
    audits: Mapped[list[Audit]] = relationship(
        back_populates="place",
        cascade=CASCADE_DELETE_ORPHAN,
    )


class AuditorProfile(Base):
    """Auditor identity/profile record owned by an auditor account."""

    __tablename__ = "auditor_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    auditor_code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), unique=True, index=True, nullable=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    age_range: Mapped[str | None] = mapped_column(String(80), nullable=True)
    gender: Mapped[str | None] = mapped_column(String(80), nullable=True)
    country: Mapped[str | None] = mapped_column(String(120), nullable=True)
    role: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    account: Mapped[Account] = relationship(back_populates="auditor_profile")
    assignments: Mapped[list[AuditorAssignment]] = relationship(
        back_populates="auditor_profile",
        cascade=CASCADE_DELETE_ORPHAN,
    )
    audits: Mapped[list[Audit]] = relationship(
        back_populates="auditor_profile",
        cascade=CASCADE_DELETE_ORPHAN,
    )


class AuditorAssignment(Base):
    """
    Assignment record for project-level or place-level auditor access.

    Exactly one of `project_id` or `place_id` must be present.
    """

    __tablename__ = "auditor_assignments"
    __table_args__ = (
        UniqueConstraint(
            "auditor_profile_id",
            "project_id",
            name="uq_auditor_assignments_auditor_project",
        ),
        UniqueConstraint(
            "auditor_profile_id",
            "place_id",
            name="uq_auditor_assignments_auditor_place",
        ),
        CheckConstraint(
            "(project_id IS NOT NULL) <> (place_id IS NOT NULL)",
            name="ck_auditor_assignments_single_scope",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    auditor_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auditor_profiles.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    place_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("places.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    audit_roles: Mapped[list[str]] = mapped_column(
        ARRAY(String(40)),
        default=lambda: ["auditor"],
        nullable=False,
    )

    auditor_profile: Mapped[AuditorProfile] = relationship(back_populates="assignments")
    project: Mapped[Project | None] = relationship(back_populates="assignments")
    place: Mapped[Place | None] = relationship(back_populates="assignments")


class Audit(Base):
    """
    Shared audit shell.

    Product-specific modules can persist detailed responses/scores in dedicated
    child tables. The JSONB columns remain as transitional compatibility caches
    while the shared dashboard layer continues to rely on lifecycle and summary
    fields.
    """

    __tablename__ = "audits"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    place_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("places.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    auditor_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auditor_profiles.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    audit_code: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    instrument_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    instrument_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[AuditStatus] = mapped_column(
        SAEnum(AuditStatus, name="shared_audit_status", create_type=False),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    responses_json: Mapped[JSONDict] = mapped_column(JSONB, default=dict, nullable=False)
    scores_json: Mapped[JSONDict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    place: Mapped[Place] = relationship(back_populates="audits")
    auditor_profile: Mapped[AuditorProfile] = relationship(back_populates="audits")
    playspace_context: Mapped[PlayspaceAuditContext | None] = relationship(
        back_populates="audit",
        cascade=CASCADE_DELETE_ORPHAN,
        uselist=False,
    )
    playspace_pre_audit_answers: Mapped[list[PlayspacePreAuditAnswer]] = relationship(
        back_populates="audit",
        cascade=CASCADE_DELETE_ORPHAN,
    )
    playspace_sections: Mapped[list[PlayspaceAuditSection]] = relationship(
        back_populates="audit",
        cascade=CASCADE_DELETE_ORPHAN,
    )


class PlayspaceAuditContext(Base):
    """Normalized one-to-one Playspace metadata stored for an audit session."""

    __tablename__ = "playspace_audit_contexts"

    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("audits.id", ondelete="CASCADE", name="fk_ps_context_audit"),
        primary_key=True,
    )
    execution_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    draft_progress_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    audit: Mapped[Audit] = relationship(back_populates="playspace_context", lazy="selectin")


class PlayspacePreAuditAnswer(Base):
    """One normalized Playspace pre-audit answer row."""

    __tablename__ = "playspace_pre_audit_answers"
    __table_args__ = (
        UniqueConstraint(
            "audit_id",
            "field_key",
            "selected_value",
            name="uq_playspace_pre_audit_answers_audit_field_value",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("audits.id", ondelete="CASCADE", name="fk_ps_pre_audit_answer_audit"),
        index=True,
        nullable=False,
    )
    field_key: Mapped[str] = mapped_column(String(80), nullable=False)
    selected_value: Mapped[str] = mapped_column(String(80), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    audit: Mapped[Audit] = relationship(back_populates="playspace_pre_audit_answers")


class PlayspaceAuditSection(Base):
    """One normalized Playspace section state row for note and child answers."""

    __tablename__ = "playspace_audit_sections"
    __table_args__ = (
        UniqueConstraint(
            "audit_id",
            "section_key",
            name="uq_playspace_audit_sections_audit_section",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("audits.id", ondelete="CASCADE", name="fk_ps_audit_section_audit"),
        index=True,
        nullable=False,
    )
    section_key: Mapped[str] = mapped_column(String(120), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    audit: Mapped[Audit] = relationship(back_populates="playspace_sections")
    question_responses: Mapped[list[PlayspaceQuestionResponse]] = relationship(
        back_populates="section",
        cascade=CASCADE_DELETE_ORPHAN,
    )


class PlayspaceQuestionResponse(Base):
    """One normalized Playspace question response row within a section."""

    __tablename__ = "playspace_question_responses"
    __table_args__ = (
        UniqueConstraint(
            "section_id",
            "question_key",
            name="uq_playspace_question_responses_section_question",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "playspace_audit_sections.id",
            ondelete="CASCADE",
            name="fk_ps_question_response_section",
        ),
        index=True,
        nullable=False,
    )
    question_key: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    section: Mapped[PlayspaceAuditSection] = relationship(back_populates="question_responses")
    scale_answers: Mapped[list[PlayspaceScaleAnswer]] = relationship(
        back_populates="question_response",
        cascade=CASCADE_DELETE_ORPHAN,
    )


class PlayspaceScaleAnswer(Base):
    """One normalized Playspace scale-answer row for a question response."""

    __tablename__ = "playspace_scale_answers"
    __table_args__ = (
        UniqueConstraint(
            "question_response_id",
            "scale_key",
            name="uq_playspace_scale_answers_question_scale",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    question_response_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "playspace_question_responses.id",
            ondelete="CASCADE",
            name="fk_ps_scale_answer_question_response",
        ),
        index=True,
        nullable=False,
    )
    scale_key: Mapped[str] = mapped_column(String(40), nullable=False)
    option_key: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    question_response: Mapped[PlayspaceQuestionResponse] = relationship(
        back_populates="scale_answers"
    )
