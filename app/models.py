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
	Date,
	DateTime,
	Float,
	ForeignKey,
	ForeignKeyConstraint,
	Index,
	Integer,
	MetaData,
	String,
	Text,
	UniqueConstraint,
	cast,
	func,
	text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import String as SAString
from sqlalchemy.types import TypeDecorator

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


class NotificationType(str, Enum):
	"""Kinds of in-app notifications surfaced to platform users."""

	ASSIGNMENT_CREATED = "ASSIGNMENT_CREATED"
	ASSIGNMENT_UPDATED = "ASSIGNMENT_UPDATED"
	AUDIT_COMPLETED = "AUDIT_COMPLETED"


class PlayspaceType(str, Enum):
	"""Type of playspace that can be audited."""

	PUBLIC = "Public Playspace"
	PRE_SCHOOL = "Pre-School Playspace"
	DESTINATION = "Destination Playspace"
	NATURE = "Nature Playspace"
	NEIGHBORHOOD = "Neighborhood Playspace"
	WATERFRONT = "Waterfront Playspace"
	SCHOOL = "School Playspace"


JSONDict = dict[str, object]


class PostgresEnumWithCast(TypeDecorator[str]):
	"""Bind PostgreSQL enum values with explicit casts for asyncpg compatibility."""

	impl = SAString
	cache_ok = True

	def __init__(self, enum_class: type[Enum], *, name: str):
		super().__init__()
		self._enum_class = enum_class
		self._enum_impl = PGEnum(enum_class, name=name, create_type=False)

	def load_dialect_impl(self, dialect):
		"""Use the underlying PostgreSQL enum type on supported dialects."""

		return dialect.type_descriptor(self._enum_impl)

	def process_bind_param(self, value, dialect):
		"""Serialize Python enum values to their string representation."""

		if value is None:
			return None
		if isinstance(value, self._enum_class):
			return value.value
		if isinstance(value, str):
			return value
		return self._enum_class(value).value

	def process_result_value(self, value, dialect):
		"""Rehydrate database enum strings into Python enum members."""

		if value is None or isinstance(value, self._enum_class):
			return value
		return self._enum_class(value)

	def bind_expression(self, bindvalue):
		"""Force bind parameters to cast to the target PostgreSQL enum type."""

		return cast(bindvalue, self._enum_impl)


ACCOUNT_TYPE_ENUM = PostgresEnumWithCast(AccountType, name="shared_account_type")
AUDIT_STATUS_ENUM = PostgresEnumWithCast(AuditStatus, name="shared_audit_status")
NOTIFICATION_TYPE_ENUM = PostgresEnumWithCast(NotificationType, name="notification_type_enum")
PLAYSPACE_TYPE_ENUM = PostgresEnumWithCast(PlayspaceType, name="playspace_type_enum")

# Shared cascade configuration for parent -> child relationships.
CASCADE_DELETE_ORPHAN: str = "all, delete-orphan"


class User(Base):
	"""Platform user used by the real YEE auth and approval flows."""

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
		ACCOUNT_TYPE_ENUM,
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
	created_at: Mapped[datetime] = mapped_column(
		DateTime(timezone=True),
		server_default=func.now(),
		nullable=False,
	)

	account: Mapped[Account | None] = relationship(back_populates="users")
	manager_profile: Mapped[ManagerProfile | None] = relationship(back_populates="user", uselist=False)
	auditor_profile: Mapped[AuditorProfile | None] = relationship(back_populates="user", uselist=False)
	notifications: Mapped[list[Notification]] = relationship(
		back_populates="user",
		cascade=CASCADE_DELETE_ORPHAN,
	)
	created_projects: Mapped[list[Project]] = relationship(
		back_populates="created_by_user",
		foreign_keys="Project.created_by_user_id",
	)


class Notification(Base):
	"""In-app notification row owned by a platform user (CASCADE with user)."""

	__tablename__ = "notifications"
	__table_args__ = (Index("ix_notifications_user_unread", "user_id", "is_read"),)

	id: Mapped[uuid.UUID] = mapped_column(
		UUID(as_uuid=True),
		primary_key=True,
		default=uuid.uuid4,
	)
	user_id: Mapped[uuid.UUID] = mapped_column(
		UUID(as_uuid=True),
		ForeignKey("users.id", ondelete="CASCADE"),
		index=True,
		nullable=False,
	)
	message: Mapped[str] = mapped_column(String(500), nullable=False)
	notification_type: Mapped[NotificationType] = mapped_column(NOTIFICATION_TYPE_ENUM, nullable=False)
	is_read: Mapped[bool] = mapped_column(
		Boolean,
		default=False,
		server_default="false",
		nullable=False,
		index=True,
	)
	related_entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
	related_entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
	created_at: Mapped[datetime] = mapped_column(
		DateTime(timezone=True),
		server_default=func.now(),
		nullable=False,
		index=True,
	)

	user: Mapped[User] = relationship(back_populates="notifications")

	def __repr__(self) -> str:
		return (
			f"<Notification(id={self.id}, user_id={self.user_id}, "
			f"type={self.notification_type}, is_read={self.is_read})>"
		)


class Account(Base):
	"""Shared tenant/account record."""

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
		ACCOUNT_TYPE_ENUM,
		nullable=False,
	)
	created_at: Mapped[datetime] = mapped_column(
		DateTime(timezone=True),
		server_default=func.now(),
		nullable=False,
	)

	users: Mapped[list[User]] = relationship(back_populates="account")
	manager_profiles: Mapped[list[ManagerProfile]] = relationship(
		back_populates="account",
		cascade=CASCADE_DELETE_ORPHAN,
	)
	projects: Mapped[list[Project]] = relationship(
		back_populates="account",
		cascade=CASCADE_DELETE_ORPHAN,
	)
	auditor_profiles: Mapped[list[AuditorProfile]] = relationship(
		back_populates="account",
		cascade=CASCADE_DELETE_ORPHAN,
	)


class ManagerProfile(Base):
	"""Profile record for managers attached to a manager account."""

	__tablename__ = "manager_profiles"

	__table_args__ = (
		Index(
			"ix_manager_profiles_account_primary_true",
			"account_id",
			unique=True,
			postgresql_where=text("is_primary = true"),
		),
	)

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
	user_id: Mapped[uuid.UUID | None] = mapped_column(
		UUID(as_uuid=True),
		ForeignKey("users.id", ondelete="SET NULL"),
		unique=True,
		nullable=True,
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
	user: Mapped[User | None] = relationship(back_populates="manager_profile")


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
	created_by_user_id: Mapped[uuid.UUID] = mapped_column(
		UUID(as_uuid=True),
		ForeignKey("users.id", ondelete="RESTRICT"),
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
	created_by_user: Mapped[User] = relationship(
		back_populates="created_projects",
		foreign_keys=[created_by_user_id],
	)
	project_place_links: Mapped[list[ProjectPlace]] = relationship(
		back_populates="project",
		cascade=CASCADE_DELETE_ORPHAN,
	)
	places: Mapped[list[Place]] = relationship(
		secondary="project_places",
		back_populates="projects",
		overlaps="project_place_links,place,project,project_place_links",
	)
	assignments: Mapped[list[AuditorAssignment]] = relationship(
		back_populates="project",
	)
	audits: Mapped[list[Audit]] = relationship(
		back_populates="project",
	)

	@property
	def description(self) -> str | None:
		return self.overview

	@description.setter
	def description(self, value: str | None) -> None:
		self.overview = value


class Place(Base):
	"""Shared place/location model used by both product databases."""

	__tablename__ = "places"

	id: Mapped[uuid.UUID] = mapped_column(
		UUID(as_uuid=True),
		primary_key=True,
		default=uuid.uuid4,
	)
	name: Mapped[str] = mapped_column(String(200), nullable=False)
	city: Mapped[str | None] = mapped_column(String(120), nullable=True)
	province: Mapped[str | None] = mapped_column(String(120), nullable=True)
	country: Mapped[str | None] = mapped_column(String(120), nullable=True)
	postal_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
	address: Mapped[str | None] = mapped_column(Text, nullable=True)
	place_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
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

	project_place_links: Mapped[list[ProjectPlace]] = relationship(
		back_populates="place",
		cascade=CASCADE_DELETE_ORPHAN,
	)
	projects: Mapped[list[Project]] = relationship(
		secondary="project_places",
		back_populates="places",
		overlaps="project_place_links,project,place,project_place_links",
	)
	assignments: Mapped[list[AuditorAssignment]] = relationship(
		back_populates="place",
		cascade=CASCADE_DELETE_ORPHAN,
	)
	audits: Mapped[list[Audit]] = relationship(
		back_populates="place",
		cascade=CASCADE_DELETE_ORPHAN,
	)

	@property
	def notes(self) -> str | None:
		return self.auditor_description

	@notes.setter
	def notes(self, value: str | None) -> None:
		self.auditor_description = value


class ProjectPlace(Base):
	"""Join row linking one place to one project."""

	__tablename__ = "project_places"
	__mapper_args__ = {"confirm_deleted_rows": False}

	project_id: Mapped[uuid.UUID] = mapped_column(
		UUID(as_uuid=True),
		ForeignKey("projects.id", ondelete="CASCADE"),
		primary_key=True,
	)
	place_id: Mapped[uuid.UUID] = mapped_column(
		UUID(as_uuid=True),
		ForeignKey("places.id", ondelete="CASCADE"),
		primary_key=True,
	)
	linked_at: Mapped[datetime] = mapped_column(
		DateTime(timezone=True),
		server_default=func.now(),
		nullable=False,
	)

	project: Mapped[Project] = relationship(
		back_populates="project_place_links",
		overlaps="places,projects",
	)
	place: Mapped[Place] = relationship(
		back_populates="project_place_links",
		overlaps="places,projects",
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
	created_at: Mapped[datetime] = mapped_column(
		DateTime(timezone=True),
		server_default=func.now(),
		nullable=False,
	)

	account: Mapped[Account] = relationship(back_populates="auditor_profiles")
	user: Mapped[User | None] = relationship(back_populates="auditor_profile")
	assignments: Mapped[list[AuditorAssignment]] = relationship(
		back_populates="auditor_profile",
		cascade=CASCADE_DELETE_ORPHAN,
	)
	audits: Mapped[list[Audit]] = relationship(
		back_populates="auditor_profile",
		cascade=CASCADE_DELETE_ORPHAN,
	)
	invites: Mapped[list[AuditorInvite]] = relationship(
		back_populates="auditor",
		passive_deletes=True,
	)


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


class ManagerInvite(Base):
	__tablename__ = "manager_invites"

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
	accepted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
		UUID(as_uuid=True),
		ForeignKey("users.id", ondelete="SET NULL"),
		index=True,
		nullable=True,
	)
	email: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
	token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
	created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
	expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
	accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

	account: Mapped[Account] = relationship()
	invited_by_user: Mapped[User] = relationship(foreign_keys=[invited_by_user_id])
	accepted_by_user: Mapped[User | None] = relationship(foreign_keys=[accepted_by_user_id])


class AuditorAssignment(Base):
	"""
	Assignment record: exactly one auditor is tied to one project–place pair.

	``place_id`` is always required. Uniqueness of
	``(auditor_profile_id, project_id, place_id)`` is enforced with a single
	database unique constraint (see ``uq_auditor_assignments_auditor_project_place``).
	"""

	__tablename__ = "auditor_assignments"
	__table_args__ = (
		UniqueConstraint(
			"auditor_profile_id",
			"project_id",
			"place_id",
			name="uq_auditor_assignments_auditor_project_place",
		),
		ForeignKeyConstraint(
			["project_id", "place_id"],
			["project_places.project_id", "project_places.place_id"],
			name="fk_auditor_assignments_project_place_pair",
			ondelete="CASCADE",
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
	project_id: Mapped[uuid.UUID] = mapped_column(
		UUID(as_uuid=True),
		ForeignKey("projects.id", ondelete="CASCADE"),
		index=True,
		nullable=False,
	)
	place_id: Mapped[uuid.UUID] = mapped_column(
		UUID(as_uuid=True),
		ForeignKey("places.id", ondelete="CASCADE"),
		index=True,
		nullable=False,
	)
	assigned_at: Mapped[datetime] = mapped_column(
		DateTime(timezone=True),
		server_default=func.now(),
		nullable=False,
	)

	auditor_profile: Mapped[AuditorProfile] = relationship(back_populates="assignments")
	project: Mapped[Project | None] = relationship(back_populates="assignments")
	place: Mapped[Place] = relationship(back_populates="assignments")

	@property
	def auditor_id(self) -> uuid.UUID:
		return self.auditor_profile_id


class Audit(Base):
	"""
	Shared audit shell.

	Product-specific modules can persist detailed responses/scores in dedicated
	child tables. The JSONB columns remain as transitional compatibility caches
	while the shared dashboard layer continues to rely on lifecycle and summary
	fields.
	"""

	__tablename__ = "audits"
	__table_args__ = (
		UniqueConstraint(
			"project_id",
			"place_id",
			"auditor_profile_id",
			name="uq_audits_project_place_auditor",
		),
		ForeignKeyConstraint(
			["project_id", "place_id"],
			["project_places.project_id", "project_places.place_id"],
			name="fk_audits_project_place_pair",
			ondelete="CASCADE",
		),
	)

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
		AUDIT_STATUS_ENUM,
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

	project: Mapped[Project] = relationship(back_populates="audits")
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

	@property
	def auditor_id(self) -> uuid.UUID:
		return self.auditor_profile_id


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

	question_response: Mapped[PlayspaceQuestionResponse] = relationship(back_populates="scale_answers")


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


# Compatibility aliases for the YEE router code.
Auditor = AuditorProfile
Assignment = AuditorAssignment


class Instrument(Base):
	"""
	Source of Truth for an audit instrument (e.g. PVUA).

	Stored as a full versioned JSON object in the database to support
	dynamic UI rendering and validation across web and mobile.
	"""

	__tablename__ = "instruments"

	id: Mapped[uuid.UUID] = mapped_column(
		UUID(as_uuid=True),
		primary_key=True,
		default=uuid.uuid4,
		server_default=text("gen_random_uuid()"),
	)
	instrument_key: Mapped[str] = mapped_column(String(255), nullable=False)
	instrument_version: Mapped[str] = mapped_column(String(50), nullable=False)
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
	is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
	content: Mapped[JSONDict] = mapped_column(JSONB, nullable=False)

	def __repr__(self) -> str:
		return f"<Instrument(id='{self.id}', key='{self.instrument_key}', version='{self.instrument_version}')>"
