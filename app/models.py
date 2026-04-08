"""
SQLAlchemy ORM models for the Audit Tools backend.

Key design choices:
- UUID primary keys across all tables.
- PostgreSQL JSONB columns for flexible instrument responses/scores.
- Explicit association table (`Assignment`) for auditor <-> place assignments.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
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
    """Account types for `User` (high-level access class)."""

    ADMIN = "ADMIN"
    MANAGER = "MANAGER"
    AUDITOR = "AUDITOR"


class AuditStatus(str, Enum):
    """Lifecycle states for `Audit`."""

    IN_PROGRESS = "IN_PROGRESS"
    SUBMITTED = "SUBMITTED"


JSONDict = dict[str, object]

# Shared cascade configuration for parent -> child relationships.
CASCADE_DELETE_ORPHAN: str = "all, delete-orphan"


class User(Base):
    """Authenticated user of the system."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    account_type: Mapped[AccountType] = mapped_column(
        SAEnum(AccountType, name="account_type"),
        nullable=False,
    )
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    email_verification_token_hash: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    email_verification_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    failed_login_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    approved: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    profile_completed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    profile_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Optional one-to-one link: an auditor profile can be associated to a user account.
    account: Mapped[Account | None] = relationship(back_populates="users")
    auditor_profile: Mapped[Auditor | None] = relationship(
        back_populates="user",
        uselist=False,
    )


class Account(Base):
    """A customer/org account that owns projects and auditor profiles."""

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    users: Mapped[list[User]] = relationship(back_populates="account")
    projects: Mapped[list[Project]] = relationship(
        back_populates="account",
        cascade=CASCADE_DELETE_ORPHAN,
    )
    auditors: Mapped[list[Auditor]] = relationship(
        back_populates="account",
        cascade=CASCADE_DELETE_ORPHAN,
    )


class Project(Base):
    """A project (under an account) which contains auditable places."""

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
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    account: Mapped[Account] = relationship(back_populates="projects")
    places: Mapped[list[Place]] = relationship(
        back_populates="project",
        cascade=CASCADE_DELETE_ORPHAN,
    )


class Place(Base):
    """A physical place/location to be audited within a project."""

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
    address: Mapped[str] = mapped_column(String(500), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped[Project] = relationship(back_populates="places")

    assignments: Mapped[list[Assignment]] = relationship(
        back_populates="place",
        cascade=CASCADE_DELETE_ORPHAN,
    )
    audits: Mapped[list[Audit]] = relationship(
        back_populates="place",
        cascade=CASCADE_DELETE_ORPHAN,
    )


class Auditor(Base):
    """An auditor profile under an account, optionally tied to a user."""

    __tablename__ = "auditors"

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
    auditor_code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        unique=True,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    account: Mapped[Account] = relationship(back_populates="auditors")
    user: Mapped[User | None] = relationship(back_populates="auditor_profile")

    assignments: Mapped[list[Assignment]] = relationship(
        back_populates="auditor",
        cascade=CASCADE_DELETE_ORPHAN,
    )
    audits: Mapped[list[Audit]] = relationship(
        back_populates="auditor",
        cascade=CASCADE_DELETE_ORPHAN,
    )
    invites: Mapped[list[AuditorInvite]] = relationship(
        back_populates="auditor",
    )


class AuditorInvite(Base):
    """Manager-issued invite for an auditor account within an account scope."""

    __tablename__ = "auditor_invites"

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
    invited_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    auditor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auditors.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    email: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    account: Mapped[Account] = relationship()
    invited_by_user: Mapped[User] = relationship()
    auditor: Mapped[Auditor | None] = relationship(back_populates="invites")


class Assignment(Base):
    """Assignment of an `Auditor` to a `Place`."""

    __tablename__ = "assignments"
    __table_args__ = (
        UniqueConstraint("auditor_id", "place_id", name="uq_assignment_auditor_place"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    auditor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auditors.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    place_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("places.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    auditor: Mapped[Auditor] = relationship(back_populates="assignments")
    place: Mapped[Place] = relationship(back_populates="assignments")


class Instrument(Base):
    """A versioned audit instrument (e.g., questionnaire template)."""

    __tablename__ = "instruments"
    __table_args__ = (UniqueConstraint("key", "version", name="uq_instrument_key_version"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    key: Mapped[str] = mapped_column(String(50), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    audits: Mapped[list[Audit]] = relationship(
        back_populates="instrument",
        cascade=CASCADE_DELETE_ORPHAN,
    )


class Audit(Base):
    """An instance of an audit for a place using a specific instrument."""

    __tablename__ = "audits"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    instrument_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("instruments.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    place_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("places.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    auditor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auditors.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[AuditStatus] = mapped_column(
        SAEnum(AuditStatus, name="audit_status"),
        nullable=False,
    )
    responses_json: Mapped[JSONDict] = mapped_column(JSONB, default=dict, nullable=False)
    scores_json: Mapped[JSONDict] = mapped_column(JSONB, default=dict, nullable=False)

    instrument: Mapped[Instrument] = relationship(back_populates="audits")
    place: Mapped[Place] = relationship(back_populates="audits")
    auditor: Mapped[Auditor] = relationship(back_populates="audits")


class YeeAuditSubmission(Base):
    """Stored YEE audit submission with computed scores."""

    __tablename__ = "yee_audit_submissions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    auditor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auditors.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    place_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("places.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    participant_info_json: Mapped[JSONDict] = mapped_column(JSONB, default=dict, nullable=False)
    responses_json: Mapped[JSONDict] = mapped_column(JSONB, default=dict, nullable=False)
    section_scores_json: Mapped[JSONDict] = mapped_column(JSONB, default=dict, nullable=False)
    total_score: Mapped[int] = mapped_column(nullable=False)

    auditor: Mapped[Auditor] = relationship()
    place: Mapped[Place] = relationship()
