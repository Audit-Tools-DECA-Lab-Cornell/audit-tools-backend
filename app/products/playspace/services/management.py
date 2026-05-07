"""
Manager/admin write-path service for Playspace dashboard workflows.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth_security import generate_email_verification_token, hash_password, hash_verification_token
from app.core.actors import (
	CurrentUserContext,
	CurrentUserRole,
	require_manager_or_admin_user,
)
from app.models import (
	Account,
	AccountType,
	AuditorProfile,
	ManagerInvite,
	ManagerProfile,
	Place,
	Project,
	ProjectPlace,
	User,
)
from app.products.playspace.schemas import (
	AccountManagementResponse,
	AccountUpdateRequest,
	AuditorProfileCreateRequest,
	AuditorProfileDetailResponse,
	AuditorProfileUpdateRequest,
	ManagerInviteCreateRequest,
	ManagerInviteCreatedResponse,
	ManagerInviteListItemResponse,
	PlaceCreateRequest,
	PlaceDetailResponse,
	PlaceUpdateRequest,
	ProjectCreateRequest,
	ProjectDetailResponse,
	ProjectUpdateRequest,
	SavedPlaceReportEntry,
	SavePlaceReportRequest,
)
from app.email_service import send_auditor_credentials_email, send_manager_invite_email
from app.products.playspace.services.privacy import mask_email


class PlayspaceManagementService:
	"""Write operations for manager/admin dashboard workflows."""

	def __init__(self, session: AsyncSession):
		self._session = session

	def _require_manager_or_admin(self, actor: CurrentUserContext) -> None:
		"""Guard write endpoints to manager/admin actors."""

		require_manager_or_admin_user(actor)

	def _resolve_target_account_id(
		self,
		*,
		actor: CurrentUserContext,
		requested_account_id: uuid.UUID | None,
	) -> uuid.UUID:
		"""Resolve target account id based on actor role."""

		if actor.role is CurrentUserRole.ADMIN:
			if requested_account_id is None:
				raise HTTPException(
					status_code=status.HTTP_400_BAD_REQUEST,
					detail="account_id is required for admin project creation.",
				)
			return requested_account_id

		if actor.account_id is None:
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail="Manager account context is required.",
			)
		if requested_account_id is not None and requested_account_id != actor.account_id:
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail="Managers can only create records in their own account.",
			)
		return actor.account_id

	def _ensure_account_access(self, *, actor: CurrentUserContext, account_id: uuid.UUID) -> None:
		"""Ensure actor can mutate resources under target account."""

		if actor.role is CurrentUserRole.ADMIN:
			return
		if actor.account_id != account_id:
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail="This actor cannot modify resources in the requested account.",
			)

	async def _get_account(self, account_id: uuid.UUID) -> Account:
		"""Load an account or raise 404."""

		account_result = await self._session.execute(select(Account).where(Account.id == account_id))
		account = account_result.scalar_one_or_none()
		if account is None:
			raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
		return account

	async def _get_project(self, project_id: uuid.UUID) -> Project:
		"""Load a project or raise 404."""

		project_result = await self._session.execute(select(Project).where(Project.id == project_id))
		project = project_result.scalar_one_or_none()
		if project is None:
			raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
		return project

	async def _get_place(self, place_id: uuid.UUID) -> Place:
		"""Load a place or raise 404."""

		place_result = await self._session.execute(select(Place).where(Place.id == place_id))
		place = place_result.scalar_one_or_none()
		if place is None:
			raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Place not found.")
		return place

	async def _get_auditor_profile(self, auditor_profile_id: uuid.UUID) -> AuditorProfile:
		"""Load an auditor profile or raise 404."""

		profile_result = await self._session.execute(
			select(AuditorProfile).where(AuditorProfile.id == auditor_profile_id)
		)
		profile = profile_result.scalar_one_or_none()
		if profile is None:
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail="Auditor profile not found.",
			)
		return profile

	@staticmethod
	def _generate_auditor_code(account_name: str) -> str:
		"""Return a non-sequential auditor code derived from the account name.

		Format: ``AUD-{ORG}-{YY}-{NNNNNNNN}``
		- ORG		— uppercase initials from the first letter of each word (e.g. "Auckland Play Collective" → "APC").
		- YY		— two-digit current UTC year.
		- NNNNNNNN	— cryptographically random 8-digit number (10000000–99999999) that prevents auditor enumeration.

		Uniqueness is enforced by the duplicate-code check that follows this
		call. A collision is statistically negligible given the 90 000 000-value
		space and the typical per-org auditor count.
		"""

		words = account_name.strip().split()
		org_initials = "".join(w[0].upper() for w in words if w) or "ORG"
		two_digit_year = str(datetime.now(timezone.utc).year % 100).zfill(2)
		sequence = secrets.randbelow(90_000_000) + 10_000_000
		return f"AUD-{org_initials}-{two_digit_year}-{sequence}"

	@staticmethod
	def _serialize_account(account: Account) -> AccountManagementResponse:
		"""Serialize a privacy-safe account payload."""

		return AccountManagementResponse(
			id=account.id,
			name=account.name,
			email_masked=mask_email(account.email),
			account_type=account.account_type,
			created_at=account.created_at,
		)

	@staticmethod
	def _serialize_project(project: Project) -> ProjectDetailResponse:
		"""Serialize a project detail payload."""

		return ProjectDetailResponse(
			id=project.id,
			account_id=project.account_id,
			name=project.name,
			overview=project.overview,
			place_types=list(project.place_types),
			start_date=project.start_date,
			end_date=project.end_date,
			est_places=project.est_places,
			est_auditors=project.est_auditors,
			auditor_description=project.auditor_description,
			created_by_user_id=project.created_by_user_id,
			created_at=project.created_at,
		)

	@staticmethod
	def _serialize_place(place: Place, projects: list[Project]) -> PlaceDetailResponse:
		"""Serialize a place detail payload."""

		saved_reports = [SavedPlaceReportEntry.model_validate(entry) for entry in (place.saved_place_reports or [])]

		return PlaceDetailResponse(
			id=place.id,
			project_ids=[project.id for project in projects],
			project_names=[project.name for project in projects],
			name=place.name,
			city=place.city,
			province=place.province,
			country=place.country,
			postal_code=place.postal_code,
			address=place.address,
			place_type=place.place_type,
			lat=place.lat,
			lng=place.lng,
			start_date=place.start_date,
			end_date=place.end_date,
			est_auditors=place.est_auditors,
			auditor_description=place.auditor_description,
			saved_place_reports=saved_reports,
			created_at=place.created_at,
		)

	async def _get_projects_for_place(self, place_id: uuid.UUID) -> list[Project]:
		"""Load all linked projects for one place in stable display order."""

		project_result = await self._session.execute(
			select(Project)
			.join(ProjectPlace, Project.id == ProjectPlace.project_id)
			.where(ProjectPlace.place_id == place_id)
			.order_by(Project.name.asc(), Project.id.asc())
		)
		return list(project_result.scalars().all())

	async def _sync_project_place_types(self, project_id: uuid.UUID) -> None:
		"""Recompute project.place_types from the place_type values of all linked places.

		Called after any place create, update, or delete so the project's denormalized
		place_types column always reflects the actual places in the project.
		"""

		place_type_result = await self._session.execute(
			select(Place.place_type)
			.join(ProjectPlace, ProjectPlace.place_id == Place.id)
			.where(ProjectPlace.project_id == project_id, Place.place_type.is_not(None))
		)
		computed_types = sorted({row[0] for row in place_type_result.fetchall()})
		project = await self._get_project(project_id)
		project.place_types = computed_types

	async def _validate_project_ids(self, project_ids: list[uuid.UUID]) -> list[Project]:
		"""Load requested projects, requiring at least one and a shared owning account."""

		normalized_project_ids = list(dict.fromkeys(project_ids))
		if not normalized_project_ids:
			raise HTTPException(
				status_code=status.HTTP_400_BAD_REQUEST,
				detail="At least one project_id is required for a place.",
			)

		project_result = await self._session.execute(select(Project).where(Project.id.in_(normalized_project_ids)))
		project_by_id = {project.id: project for project in project_result.scalars().all()}
		ordered_projects: list[Project] = []
		for project_id in normalized_project_ids:
			project = project_by_id.get(project_id)
			if project is None:
				raise HTTPException(
					status_code=status.HTTP_404_NOT_FOUND,
					detail="Project not found.",
				)
			ordered_projects.append(project)

		account_ids = {project.account_id for project in ordered_projects}
		if len(account_ids) != 1:
			raise HTTPException(
				status_code=status.HTTP_400_BAD_REQUEST,
				detail="All projects linked to one place must belong to the same account.",
			)

		return ordered_projects

	@staticmethod
	def _serialize_auditor_profile(
		profile: AuditorProfile,
	) -> AuditorProfileDetailResponse:
		"""Serialize a privacy-safe auditor profile payload."""

		if profile.account_id is None:
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail="AuditorProfile has no linked account.",
			)

		return AuditorProfileDetailResponse(
			id=profile.id,
			account_id=profile.account_id,
			auditor_code=profile.auditor_code,
			email_masked=mask_email(profile.email),
			age_range=profile.age_range,
			gender=profile.gender,
			country=profile.country,
			role=profile.role,
			created_at=profile.created_at,
		)

	async def create_project(
		self,
		*,
		actor: CurrentUserContext,
		payload: ProjectCreateRequest,
	) -> ProjectDetailResponse:
		"""Create one project row."""

		self._require_manager_or_admin(actor)
		account_id = self._resolve_target_account_id(
			actor=actor,
			requested_account_id=payload.account_id,
		)
		await self._get_account(account_id)

		if actor.user_id is None:
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail="Authenticated user context is required to create a project.",
			)

		project = Project(
			account_id=account_id,
			created_by_user_id=actor.user_id,
			name=payload.name,
			overview=payload.overview,
			place_types=payload.place_types,
			start_date=payload.start_date,
			end_date=payload.end_date,
			est_places=payload.est_places,
			est_auditors=payload.est_auditors,
			auditor_description=payload.auditor_description,
		)
		self._session.add(project)
		await self._session.commit()
		await self._session.refresh(project)
		return self._serialize_project(project)

	async def update_account(
		self,
		*,
		actor: CurrentUserContext,
		account_id: uuid.UUID,
		payload: AccountUpdateRequest,
	) -> AccountManagementResponse:
		"""Update one account row."""

		self._require_manager_or_admin(actor)
		account = await self._get_account(account_id)
		self._ensure_account_access(actor=actor, account_id=account.id)

		updates = payload.model_dump(exclude_unset=True)
		next_email = updates.get("email")
		if isinstance(next_email, str) and next_email != account.email:
			duplicate_account_query = await self._session.execute(
				select(Account).where(Account.email == next_email, Account.id != account.id)
			)
			if duplicate_account_query.scalar_one_or_none() is not None:
				raise HTTPException(
					status_code=status.HTTP_409_CONFLICT,
					detail="Email is already in use by another account.",
				)

		for key, value in updates.items():
			setattr(account, key, value)

		await self._session.commit()
		await self._session.refresh(account)
		return self._serialize_account(account)

	async def update_project(
		self,
		*,
		actor: CurrentUserContext,
		project_id: uuid.UUID,
		payload: ProjectUpdateRequest,
	) -> ProjectDetailResponse:
		"""Update one project row."""

		self._require_manager_or_admin(actor)
		project = await self._get_project(project_id)
		self._ensure_account_access(actor=actor, account_id=project.account_id)
		updates = payload.model_dump(exclude_unset=True)
		for key, value in updates.items():
			if key == "place_types" and value is None:
				continue
			setattr(project, key, value)
		await self._session.commit()
		await self._session.refresh(project)
		return self._serialize_project(project)

	async def delete_project(
		self,
		*,
		actor: CurrentUserContext,
		project_id: uuid.UUID,
	) -> None:
		"""Delete one project row."""

		self._require_manager_or_admin(actor)
		project = await self._get_project(project_id)
		self._ensure_account_access(actor=actor, account_id=project.account_id)
		await self._session.delete(project)
		await self._session.commit()

	async def create_place(
		self,
		*,
		actor: CurrentUserContext,
		payload: PlaceCreateRequest,
	) -> PlaceDetailResponse:
		"""Create one place row and link it to one or more projects."""

		self._require_manager_or_admin(actor)
		projects = await self._validate_project_ids(payload.project_ids)
		self._ensure_account_access(actor=actor, account_id=projects[0].account_id)
		place = Place(
			name=payload.name,
			city=payload.city,
			province=payload.province,
			country=payload.country,
			postal_code=payload.postal_code,
			address=payload.address,
			place_type=payload.place_type,
			lat=payload.lat,
			lng=payload.lng,
			start_date=payload.start_date,
			end_date=payload.end_date,
			est_auditors=payload.est_auditors,
			auditor_description=payload.auditor_description,
		)
		self._session.add(place)
		await self._session.flush()
		for project in projects:
			self._session.add(ProjectPlace(project_id=project.id, place_id=place.id))
		await self._session.commit()
		await self._session.refresh(place)
		for project in projects:
			await self._sync_project_place_types(project.id)
		await self._session.commit()
		return self._serialize_place(place, projects)

	async def update_place(
		self,
		*,
		actor: CurrentUserContext,
		place_id: uuid.UUID,
		payload: PlaceUpdateRequest,
	) -> PlaceDetailResponse:
		"""Update one place row."""

		self._require_manager_or_admin(actor)
		place = await self._get_place(place_id)
		current_projects = await self._get_projects_for_place(place.id)
		if not current_projects:
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail="The place is not linked to any project.",
			)
		self._ensure_account_access(actor=actor, account_id=current_projects[0].account_id)
		updates = payload.model_dump(exclude_unset=True)
		next_project_ids = updates.pop("project_ids", None)
		for key, value in updates.items():
			setattr(place, key, value)

		response_projects = current_projects
		if next_project_ids is not None:
			response_projects = await self._validate_project_ids(next_project_ids)
			self._ensure_account_access(actor=actor, account_id=response_projects[0].account_id)
			link_result = await self._session.execute(select(ProjectPlace).where(ProjectPlace.place_id == place.id))
			existing_links = link_result.scalars().all()
			existing_project_ids = {link.project_id for link in existing_links}
			next_project_id_set = {project.id for project in response_projects}
			for existing_link in existing_links:
				if existing_link.project_id not in next_project_id_set:
					await self._session.delete(existing_link)
			for project in response_projects:
				if project.id not in existing_project_ids:
					self._session.add(ProjectPlace(project_id=project.id, place_id=place.id))

		await self._session.commit()
		await self._session.refresh(place)
		for project in response_projects:
			await self._sync_project_place_types(project.id)
		await self._session.commit()
		return self._serialize_place(place, response_projects)

	async def delete_place(
		self,
		*,
		actor: CurrentUserContext,
		place_id: uuid.UUID,
	) -> None:
		"""Delete one place row."""

		self._require_manager_or_admin(actor)
		place = await self._get_place(place_id)
		linked_projects = await self._get_projects_for_place(place.id)
		if not linked_projects:
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail="The place is not linked to any project.",
			)
		self._ensure_account_access(actor=actor, account_id=linked_projects[0].account_id)
		linked_project_ids = [project.id for project in linked_projects]
		await self._session.delete(place)
		await self._session.commit()
		for project_id in linked_project_ids:
			await self._sync_project_place_types(project_id)
		await self._session.commit()

	async def create_auditor_profile(
		self,
		*,
		actor: CurrentUserContext,
		payload: AuditorProfileCreateRequest,
	) -> AuditorProfileDetailResponse:
		"""Create one auditor User + profile pair under the acting manager's account.

		Auditors no longer have their own Account. The new User and AuditorProfile
		both receive ``account_id = actor.account_id`` so the auditor belongs to the
		manager's organisation.
		"""

		self._require_manager_or_admin(actor)

		# Resolve the manager/admin's account that will own this auditor.
		# Managers always use their own account; admins must supply account_id.
		target_account_id = self._resolve_target_account_id(
			actor=actor,
			requested_account_id=payload.account_id,
		)

		now = datetime.now(timezone.utc)

		# Resolve or auto-generate the auditor code.
		if payload.auditor_code is not None:
			auditor_code = payload.auditor_code
		else:
			account = await self._get_account(target_account_id)
			auditor_code = self._generate_auditor_code(account.name)

		# Reject if the auditor_code is already taken.
		duplicate_code_query = await self._session.execute(
			select(AuditorProfile).where(AuditorProfile.auditor_code == auditor_code)
		)
		if duplicate_code_query.scalar_one_or_none() is not None:
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail="auditor_code is already in use.",
			)

		# Reject if an AuditorProfile already exists for this email.
		duplicate_profile_email_query = await self._session.execute(
			select(AuditorProfile).where(AuditorProfile.email == payload.email)
		)
		if duplicate_profile_email_query.scalar_one_or_none() is not None:
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail="An auditor profile already exists for this email.",
			)

		# Reject if a User with this email already exists.
		duplicate_user_query = await self._session.execute(select(User).where(User.email == payload.email))
		if duplicate_user_query.scalar_one_or_none() is not None:
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail="Email is already in use.",
			)

		temporary_password = secrets.token_urlsafe(16)
		user = User(
			email=payload.email,
			password_hash=hash_password(temporary_password),
			account_id=target_account_id,
			account_type=AccountType.AUDITOR,
			name=payload.full_name,
			email_verified=True,
			email_verified_at=now,
			failed_login_attempts=0,
			approved=True,
			approved_at=now,
			profile_completed=False,
		)
		self._session.add(user)
		await self._session.flush()

		profile = AuditorProfile(
			account_id=target_account_id,
			user_id=user.id,
			auditor_code=auditor_code,
			email=payload.email,
			full_name=payload.full_name,
			age_range=payload.age_range,
			gender=payload.gender,
			country=payload.country,
			role=payload.role,
		)
		self._session.add(profile)
		await self._session.commit()
		await self._session.refresh(profile)

		# Deliver credentials to the new auditor via email and CC the admin
		# notification address (ADMIN_NOTIFICATION_EMAIL env var).
		# Fire-and-forget: a delivery failure never blocks the response.
		send_auditor_credentials_email(
			to_email=payload.email,
			full_name=payload.full_name,
			auditor_code=auditor_code,
			temporary_password=temporary_password,
			platform="Playspace Audit Tools",
		)

		return self._serialize_auditor_profile(profile).model_copy(update={"temporary_password": temporary_password})

	async def update_auditor_profile(
		self,
		*,
		actor: CurrentUserContext,
		auditor_profile_id: uuid.UUID,
		payload: AuditorProfileUpdateRequest,
	) -> AuditorProfileDetailResponse:
		"""Update one auditor profile."""

		self._require_manager_or_admin(actor)
		profile = await self._get_auditor_profile(auditor_profile_id)

		updates = payload.model_dump(exclude_unset=True)
		if "email" in updates and updates["email"] is not None:
			duplicate_profile_query = await self._session.execute(
				select(AuditorProfile).where(
					AuditorProfile.email == updates["email"],
					AuditorProfile.id != profile.id,
				)
			)
			if duplicate_profile_query.scalar_one_or_none() is not None:
				raise HTTPException(
					status_code=status.HTTP_409_CONFLICT,
					detail="Email is already in use by another auditor profile.",
				)
			# Also reject if a User with this email already exists.
			duplicate_user_query = await self._session.execute(
				select(User).where(User.email == updates["email"], User.id != profile.user_id)
			)
			if duplicate_user_query.scalar_one_or_none() is not None:
				raise HTTPException(
					status_code=status.HTTP_409_CONFLICT,
					detail="Email is already in use by another user.",
				)

		if "auditor_code" in updates and updates["auditor_code"] is not None:
			duplicate_code_query = await self._session.execute(
				select(AuditorProfile).where(
					AuditorProfile.auditor_code == updates["auditor_code"],
					AuditorProfile.id != profile.id,
				)
			)
			if duplicate_code_query.scalar_one_or_none() is not None:
				raise HTTPException(
					status_code=status.HTTP_409_CONFLICT,
					detail="auditor_code is already in use.",
				)

		for key, value in updates.items():
			setattr(profile, key, value)

		await self._session.commit()
		await self._session.refresh(profile)
		return self._serialize_auditor_profile(profile)

	async def delete_auditor_profile(
		self,
		*,
		actor: CurrentUserContext,
		auditor_profile_id: uuid.UUID,
	) -> None:
		"""Remove an auditor from a manager account without destroying records.

		The AuditorProfile and its linked User are NOT deleted.  All historical
		data — submissions, audits, assignments — is preserved for reporting.

		What changes: ``AuditorProfile.account_id`` is set to NULL so the profile
		disappears from every account-scoped list; ``User.account_id`` is set to
		NULL so the auditor can no longer authenticate.

		The Account itself is never touched.
		"""

		self._require_manager_or_admin(actor)
		profile = await self._get_auditor_profile(auditor_profile_id)

		# Managers can only remove auditors from their own account.
		if actor.role is not CurrentUserRole.ADMIN:
			if actor.account_id != profile.account_id:
				raise HTTPException(
					status_code=status.HTTP_403_FORBIDDEN,
					detail="You can only remove auditors from your own account.",
				)

		# Unlink the auditor profile from the account.
		profile.account_id = None

		# Revoke the linked user's account association so they cannot log in.
		if profile.user_id is not None:
			user_result = await self._session.execute(select(User).where(User.id == profile.user_id))
			user = user_result.scalar_one_or_none()
			if user is not None:
				user.account_id = None

		await self._session.commit()

	######################################################################################
	################################### Place Reports ####################################
	######################################################################################

	async def save_place_report(
		self,
		*,
		actor: CurrentUserContext,
		place_id: uuid.UUID,
		payload: SavePlaceReportRequest,
	) -> PlaceDetailResponse:
		"""Append a place report entry to a place's saved_place_reports list."""

		self._require_manager_or_admin(actor)
		place = await self._get_place(place_id)
		current_projects = await self._get_projects_for_place(place.id)
		if not current_projects:
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail="The place is not linked to any project.",
			)
		self._ensure_account_access(actor=actor, account_id=current_projects[0].account_id)

		if payload.report_type == "combined":
			if payload.audit_id is None or payload.survey_id is None:
				raise HTTPException(
					status_code=status.HTTP_400_BAD_REQUEST,
					detail="Combined reports require both audit_id and survey_id.",
				)
		elif payload.report_type == "full_assessment":
			if payload.submission_id is None:
				raise HTTPException(
					status_code=status.HTTP_400_BAD_REQUEST,
					detail="Full assessment reports require submission_id.",
				)

		now = datetime.now(timezone.utc)
		new_entry: dict[str, object] = {
			"report_type": payload.report_type,
			"created_at": now.isoformat(),
		}
		if payload.report_type == "combined":
			new_entry["audit_id"] = str(payload.audit_id)
			new_entry["survey_id"] = str(payload.survey_id)
		else:
			new_entry["submission_id"] = str(payload.submission_id)

		existing_reports: list[dict[str, object]] = list(place.saved_place_reports or [])
		existing_reports.append(new_entry)
		place.saved_place_reports = existing_reports

		await self._session.commit()
		await self._session.refresh(place)
		return self._serialize_place(place, current_projects)

	async def delete_place_report(
		self,
		*,
		actor: CurrentUserContext,
		place_id: uuid.UUID,
		report_index: int,
	) -> PlaceDetailResponse:
		"""Remove a place report entry by its list index."""

		self._require_manager_or_admin(actor)
		place = await self._get_place(place_id)
		current_projects = await self._get_projects_for_place(place.id)
		if not current_projects:
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail="The place is not linked to any project.",
			)
		self._ensure_account_access(actor=actor, account_id=current_projects[0].account_id)

		existing_reports: list[dict[str, object]] = list(place.saved_place_reports or [])
		if report_index < 0 or report_index >= len(existing_reports):
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail="Report index out of range.",
			)

		existing_reports.pop(report_index)
		place.saved_place_reports = existing_reports

		await self._session.commit()
		await self._session.refresh(place)
		return self._serialize_place(place, current_projects)

	######################################################################################
	################################# Manager Invites ####################################
	######################################################################################

	@staticmethod
	def _derive_invite_status(invite: ManagerInvite) -> str:
		"""Return the derived status string for a manager invite row."""

		if invite.accepted_at is not None:
			return "ACCEPTED"
		if datetime.now(timezone.utc) > invite.expires_at:
			return "EXPIRED"
		return "PENDING"

	@staticmethod
	def _serialize_manager_invite(invite: ManagerInvite) -> ManagerInviteListItemResponse:
		"""Serialize a ManagerInvite ORM row to the list-item response shape."""

		return ManagerInviteListItemResponse(
			id=invite.id,
			email=invite.email,
			status=PlayspaceManagementService._derive_invite_status(invite),
			created_at=invite.created_at,
			expires_at=invite.expires_at,
			accepted_at=invite.accepted_at,
		)

	async def _require_primary_manager(self, *, actor: CurrentUserContext) -> uuid.UUID:
		"""Raise 403 unless the authenticated actor is the primary manager of their account.

		Returns the account_id on success so callers can use it directly.
		"""

		if actor.role is not CurrentUserRole.MANAGER:
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail="Only managers can manage invites.",
			)
		if actor.account_id is None:
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail="Manager account scope is required.",
			)
		profile_result = await self._session.execute(
			select(ManagerProfile).where(
				ManagerProfile.user_id == actor.user_id,
				ManagerProfile.account_id == actor.account_id,
			)
		)
		profile = profile_result.scalar_one_or_none()
		if profile is None or not profile.is_primary:
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail="Only the primary manager can manage invites.",
			)
		return actor.account_id

	async def create_manager_invite(
		self,
		*,
		actor: CurrentUserContext,
		payload: ManagerInviteCreateRequest,
		invite_url_template: str,
	) -> ManagerInviteCreatedResponse:
		"""Create a ManagerInvite record and send the invitation email.

		``invite_url_template`` is a Python format string with a ``{token}``
		placeholder, resolved by the route handler from the FastAPI request so the
		service stays decoupled from HTTP context.
		"""

		account_id = await self._require_primary_manager(actor=actor)

		email = payload.email.strip().lower()
		if not email:
			raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email is required.")

		if actor.user_id is not None:
			self_result = await self._session.execute(select(User).where(User.id == actor.user_id))
			self_user = self_result.scalar_one_or_none()
			if self_user is not None and self_user.email.strip().lower() == email:
				raise HTTPException(
					status_code=status.HTTP_409_CONFLICT,
					detail="Use your existing credentials for this account.",
				)

		existing_user_result = await self._session.execute(select(User).where(User.email == email))
		existing_user = existing_user_result.scalar_one_or_none()
		if existing_user is not None:
			if existing_user.account_type != AccountType.MANAGER:
				raise HTTPException(
					status_code=status.HTTP_409_CONFLICT,
					detail="This email is already used by a non-manager account.",
				)
			if existing_user.account_id == account_id:
				raise HTTPException(
					status_code=status.HTTP_409_CONFLICT,
					detail="This manager already has account access.",
				)
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail="This email is already used by another manager account.",
			)

		existing_profile_result = await self._session.execute(
			select(ManagerProfile).where(ManagerProfile.email == email)
		)
		existing_profile = existing_profile_result.scalar_one_or_none()
		if existing_profile is not None:
			if existing_profile.account_id != account_id:
				raise HTTPException(
					status_code=status.HTTP_409_CONFLICT,
					detail="This email is already linked to another manager account.",
				)
			if existing_profile.user_id is not None:
				raise HTTPException(
					status_code=status.HTTP_409_CONFLICT,
					detail="This manager already has account access.",
				)

		now = datetime.now(timezone.utc)
		existing_invite_result = await self._session.execute(
			select(ManagerInvite)
			.where(
				ManagerInvite.account_id == account_id,
				ManagerInvite.email == email,
				ManagerInvite.accepted_at.is_(None),
			)
			.order_by(ManagerInvite.created_at.desc())
			.limit(1)
		)
		existing_invite = existing_invite_result.scalar_one_or_none()
		if existing_invite is not None and now <= existing_invite.expires_at:
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail="An active manager invite already exists for this email.",
			)

		token = generate_email_verification_token()
		invite = ManagerInvite(
			account_id=account_id,
			invited_by_user_id=actor.user_id,
			email=email,
			token_hash=hash_verification_token(token),
			expires_at=now + timedelta(days=7),
		)
		self._session.add(invite)
		await self._session.flush()

		invite_url = invite_url_template.format(token=token)

		# Resolve org name and inviter display name for the email context panel.
		account_result = await self._session.execute(select(Account).where(Account.id == account_id))
		account = account_result.scalar_one_or_none()
		organization_name: str | None = account.name if account is not None else None

		invited_by_name: str | None = None
		if actor.user_id is not None:
			profile_result = await self._session.execute(
				select(ManagerProfile).where(ManagerProfile.user_id == actor.user_id)
			)
			inviter_profile = profile_result.scalar_one_or_none()
			if inviter_profile is not None:
				invited_by_name = inviter_profile.full_name

		send_manager_invite_email(
			to_email=email,
			invite_url=invite_url,
			organization_name=organization_name,
			invited_by_name=invited_by_name,
		)
		await self._session.commit()
		await self._session.refresh(invite)

		return ManagerInviteCreatedResponse(
			id=invite.id,
			email=invite.email,
			expires_at=invite.expires_at,
			invite_url=invite_url,
		)

	async def list_manager_invites(
		self,
		*,
		actor: CurrentUserContext,
	) -> list[ManagerInviteListItemResponse]:
		"""Return all manager invites for the primary manager's account."""

		account_id = await self._require_primary_manager(actor=actor)
		result = await self._session.execute(
			select(ManagerInvite)
			.where(ManagerInvite.account_id == account_id)
			.order_by(ManagerInvite.created_at.desc())
		)
		invites = list(result.scalars().all())
		return [self._serialize_manager_invite(invite) for invite in invites]

	async def revoke_manager_invite(
		self,
		*,
		actor: CurrentUserContext,
		invite_id: uuid.UUID,
	) -> None:
		"""Delete a pending manager invite, preventing acceptance."""

		account_id = await self._require_primary_manager(actor=actor)
		result = await self._session.execute(
			select(ManagerInvite).where(
				ManagerInvite.id == invite_id,
				ManagerInvite.account_id == account_id,
			)
		)
		invite = result.scalar_one_or_none()
		if invite is None:
			raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found.")
		if invite.accepted_at is not None:
			raise HTTPException(
				status_code=status.HTTP_400_BAD_REQUEST,
				detail="Cannot revoke an invite that has already been accepted.",
			)
		await self._session.delete(invite)
		await self._session.commit()

	async def resend_manager_invite(
		self,
		*,
		actor: CurrentUserContext,
		invite_id: uuid.UUID,
		invite_url_template: str,
	) -> ManagerInviteListItemResponse:
		"""Regenerate the invite token, extend the expiry, and re-send the email."""

		account_id = await self._require_primary_manager(actor=actor)
		result = await self._session.execute(
			select(ManagerInvite).where(
				ManagerInvite.id == invite_id,
				ManagerInvite.account_id == account_id,
			)
		)
		invite = result.scalar_one_or_none()
		if invite is None:
			raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found.")
		if invite.accepted_at is not None:
			raise HTTPException(
				status_code=status.HTTP_400_BAD_REQUEST,
				detail="Cannot resend an invite that has already been accepted.",
			)
		now = datetime.now(timezone.utc)
		token = generate_email_verification_token()
		invite.token_hash = hash_verification_token(token)
		invite.expires_at = now + timedelta(days=7)
		await self._session.flush()
		invite_url = invite_url_template.format(token=token)

		# Resolve org name and inviter name so the resent email includes the same
		# workspace context panel as the original invite email.
		account_result = await self._session.execute(select(Account).where(Account.id == account_id))
		account = account_result.scalar_one_or_none()
		organization_name: str | None = account.name if account is not None else None

		invited_by_name: str | None = None
		if actor.user_id is not None:
			profile_result = await self._session.execute(
				select(ManagerProfile).where(ManagerProfile.user_id == actor.user_id)
			)
			inviter_profile = profile_result.scalar_one_or_none()
			if inviter_profile is not None:
				invited_by_name = inviter_profile.full_name

		send_manager_invite_email(
			to_email=invite.email,
			invite_url=invite_url,
			organization_name=organization_name,
			invited_by_name=invited_by_name,
		)
		await self._session.commit()
		await self._session.refresh(invite)
		return self._serialize_manager_invite(invite)
