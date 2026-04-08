"""
Shared SQLAlchemy ORM models for the Audit Tools backend.

This merged model layer keeps the shared-core hierarchy introduced on `master`
while preserving the real YEE auth, dashboard, invite, and submission features
from the YEE branch.
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
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class AccountType(str, Enum):
    ADMIN = "ADMIN"
    MANAGER = "MANAGER"
    AUDITOR = "AUDITOR"


class AuditStatus(str, Enum):
    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"
    SUBMITTED = "SUBMITTED"


JSONDict = dict[str, object]
CASCADE_DELETE_ORPHAN: str = "all, delete-orphan"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    account_type: Mapped[AccountType] = mapped_column(
        SAEnum(AccountType, name="shared_account_type", create_type=False),
        nullable=False,
    )
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    email_verification_token_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email_verification_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    profile_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    profile_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    account: Mapped[Account | None] = relationship(back_populates="users")
    auditor_profile: Mapped[AuditorProfile | None] = relationship(back_populates="user", uselist=False)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_type: Mapped[AccountType] = mapped_column(
        SAEnum(AccountType, name="shared_account_type", create_type=False),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    users: Mapped[list[User]] = relationship(back_populates="account")
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
    __tablename__ = "manager_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    account: Mapped[Account] = relationship(back_populates="manager_profiles")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    account: Mapped[Account] = relationship(back_populates="projects")
    places: Mapped[list[Place]] = relationship(back_populates="project", cascade=CASCADE_DELETE_ORPHAN)
    assignments: Mapped[list[AuditorAssignment]] = relationship(back_populates="project")

    @property
    def description(self) -> str | None:
        return self.overview

    @description.setter
    def description(self, value: str | None) -> None:
        self.overview = value


class Place(Base):
    __tablename__ = "places"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    project: Mapped[Project] = relationship(back_populates="places")
    assignments: Mapped[list[AuditorAssignment]] = relationship(
        back_populates="place",
        cascade=CASCADE_DELETE_ORPHAN,
    )
    audits: Mapped[list[Audit]] = relationship(back_populates="place", cascade=CASCADE_DELETE_ORPHAN)

    @property
    def address(self) -> str:
        return ", ".join(part for part in [self.city, self.province, self.country] if part) or "Address not set"

    @address.setter
    def address(self, value: str) -> None:
        self.city = value.strip() if value else None

    @property
    def notes(self) -> str | None:
        return self.auditor_description

    @notes.setter
    def notes(self, value: str | None) -> None:
        self.auditor_description = value


class AuditorProfile(Base):
    __tablename__ = "auditor_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        unique=True,
        nullable=True,
    )
    auditor_code: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), unique=True, index=True, nullable=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    age_range: Mapped[str | None] = mapped_column(String(80), nullable=True)
    gender: Mapped[str | None] = mapped_column(String(80), nullable=True)
    country: Mapped[str | None] = mapped_column(String(120), nullable=True)
    role: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    account: Mapped[Account] = relationship(back_populates="auditor_profile")
    user: Mapped[User | None] = relationship(back_populates="auditor_profile")
    assignments: Mapped[list[AuditorAssignment]] = relationship(
        back_populates="auditor_profile",
        cascade=CASCADE_DELETE_ORPHAN,
    )
    audits: Mapped[list[Audit]] = relationship(back_populates="auditor_profile", cascade=CASCADE_DELETE_ORPHAN)
    invites: Mapped[list[AuditorInvite]] = relationship(back_populates="auditor")


class AuditorInvite(Base):
    __tablename__ = "auditor_invites"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
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
        ForeignKey("auditor_profiles.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    email: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    account: Mapped[Account] = relationship()
    invited_by_user: Mapped[User] = relationship()
    auditor: Mapped[AuditorProfile | None] = relationship(back_populates="invites")


class AuditorAssignment(Base):
    __tablename__ = "auditor_assignments"
    __table_args__ = (
        UniqueConstraint("auditor_profile_id", "project_id", name="uq_auditor_assignments_auditor_project"),
        UniqueConstraint("auditor_profile_id", "place_id", name="uq_auditor_assignments_auditor_place"),
        CheckConstraint("(project_id IS NOT NULL) <> (place_id IS NOT NULL)", name="ck_auditor_assignments_single_scope"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
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
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    auditor_profile: Mapped[AuditorProfile] = relationship(back_populates="assignments")
    project: Mapped[Project | None] = relationship(back_populates="assignments")
    place: Mapped[Place | None] = relationship(back_populates="assignments")

    @property
    def auditor_id(self) -> uuid.UUID:
        return self.auditor_profile_id


class Audit(Base):
    __tablename__ = "audits"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
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
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    responses_json: Mapped[JSONDict] = mapped_column(JSONB, default=dict, nullable=False)
    scores_json: Mapped[JSONDict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    place: Mapped[Place] = relationship(back_populates="audits")
    auditor_profile: Mapped[AuditorProfile] = relationship(back_populates="audits")

    @property
    def auditor_id(self) -> uuid.UUID:
        return self.auditor_profile_id


class YeeAuditSubmission(Base):
    __tablename__ = "yee_audit_submissions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    auditor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auditor_profiles.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    place_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("places.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    participant_info_json: Mapped[JSONDict] = mapped_column(JSONB, default=dict, nullable=False)
    responses_json: Mapped[JSONDict] = mapped_column(JSONB, default=dict, nullable=False)
    section_scores_json: Mapped[JSONDict] = mapped_column(JSONB, default=dict, nullable=False)
    total_score: Mapped[int] = mapped_column(nullable=False)

    auditor: Mapped[AuditorProfile] = relationship()
    place: Mapped[Place] = relationship()


# Compatibility aliases for the YEE branch router code.
Auditor = AuditorProfile
Assignment = AuditorAssignment

