"""
Assignment-focused methods for the Playspace audit service.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.actors import (
	CurrentUserContext,
	CurrentUserRole,
	require_manager_or_admin_user,
)
from app.models import AuditorAssignment, AuditorProfile, Place, Project, ProjectPlace
from app.notification_service import NotificationService
from app.products.playspace.schemas import (
	AssignmentResponse,
	AssignmentWriteRequest,
	BulkAssignmentWriteRequest,
)

if TYPE_CHECKING:
	from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

######################################################################################
############################# Assignment Service Mixin ###############################
######################################################################################


class PlayspaceAuditAssignmentsMixin:
	"""Mixin containing auditor assignment operations for Playspace."""

	if TYPE_CHECKING:
		_session: AsyncSession

		async def _commit_and_refresh(self, instance: AuditorAssignment) -> None: ...

	async def list_assignments(
		self,
		*,
		actor: CurrentUserContext,
		auditor_profile_id: uuid.UUID,
	) -> list[AssignmentResponse]:
		"""List assignments for a profile, allowing managers and the owning auditor."""

		profile = await self._get_auditor_profile(auditor_profile_id=auditor_profile_id)
		self._ensure_profile_access(actor=actor, profile=profile)

		query = (
			select(AuditorAssignment)
			.where(AuditorAssignment.auditor_profile_id == auditor_profile_id)
			.order_by(AuditorAssignment.assigned_at.desc())
			.options(
				selectinload(AuditorAssignment.project),
				selectinload(AuditorAssignment.place),
			)
		)
		if actor.role is CurrentUserRole.MANAGER:
			if actor.account_id is None:
				raise HTTPException(
					status_code=status.HTTP_403_FORBIDDEN,
					detail="Manager account context is required for assignment access.",
				)
			query = query.where(AuditorAssignment.project.has(Project.account_id == actor.account_id))

		result = await self._session.execute(query)
		assignments = result.scalars().all()
		return [self._serialize_assignment(assignment) for assignment in assignments]

	async def create_assignment(
		self,
		*,
		actor: CurrentUserContext,
		auditor_profile_id: uuid.UUID,
		payload: AssignmentWriteRequest,
	) -> AssignmentResponse:
		"""Create a project- or project-place-scoped assignment."""

		require_manager_or_admin_user(actor)
		profile = await self._get_auditor_profile(auditor_profile_id=auditor_profile_id)
		await self._validate_assignment_scope(payload=payload)
		await self._ensure_actor_can_manage_scope(
			actor=actor,
			project_id=payload.project_id,
			place_id=payload.place_id,
		)
		await self._ensure_assignment_scope_is_unique(
			auditor_profile_id=auditor_profile_id,
			project_id=payload.project_id,
			place_id=payload.place_id,
		)

		assignment = AuditorAssignment(
			auditor_profile_id=auditor_profile_id,
			project_id=payload.project_id,
			place_id=payload.place_id,
		)
		self._session.add(assignment)
		# Persist PK before notifications: ``assignment.id`` is not guaranteed until flush.
		await self._session.flush()

		place_name_result = await self._session.execute(select(Place.name).where(Place.id == payload.place_id))
		place_name = place_name_result.scalar_one()

		if profile.user_id is not None:
			try:
				await NotificationService.create_assignment_notification(
					db=self._session,
					user_id=profile.user_id,
					assignment_id=assignment.id,
					place_name=place_name,
				)
				logger.info(
					"Created assignment notification",
					extra={
						"assignment_id": str(assignment.id),
						"auditor_profile_id": str(auditor_profile_id),
						"user_id": str(profile.user_id),
					},
				)
			except Exception as exc:
				logger.error(
					"Failed to create assignment notification",
					extra={
						"assignment_id": str(assignment.id),
						"auditor_profile_id": str(auditor_profile_id),
						"error": str(exc),
					},
				)
				raise

		try:
			await self._commit_and_refresh(assignment)
		except IntegrityError as error:
			await self._session.rollback()
			self._raise_if_duplicate_assignment_integrity_error(error)
			raise

		hydrated_assignment = await self._get_assignment_with_scope(
			assignment_id=assignment.id,
			auditor_profile_id=auditor_profile_id,
		)
		return self._serialize_assignment(hydrated_assignment)

	async def create_bulk_assignments(
		self,
		*,
		actor: CurrentUserContext,
		payload: BulkAssignmentWriteRequest,
	) -> int:
		"""Bulk create project-place assignments for multiple auditors.

		Validates the project, every auditor profile, and every place-project
		pair up-front before inserting any rows. Existing duplicate assignments
		are silently skipped.
		"""

		require_manager_or_admin_user(actor)
		await self._ensure_actor_can_manage_scope(
			actor=actor,
			project_id=payload.project_id,
			place_id=None,
		)

		project_result = await self._session.execute(select(Project.id).where(Project.id == payload.project_id))
		if project_result.scalar_one_or_none() is None:
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail="Project not found.",
			)

		profile_result = await self._session.execute(
			select(AuditorProfile.id, AuditorProfile.user_id).where(AuditorProfile.id.in_(payload.auditor_profile_ids))
		)
		profile_rows = profile_result.all()
		found_profile_ids = {row[0] for row in profile_rows}
		user_id_by_profile_id: dict[uuid.UUID, uuid.UUID | None] = {row[0]: row[1] for row in profile_rows}
		missing_profiles = set(payload.auditor_profile_ids) - found_profile_ids
		if missing_profiles:
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail="One or more auditor profiles not found.",
			)

		place_names_result = await self._session.execute(
			select(Place.id, Place.name).where(Place.id.in_(payload.place_ids))
		)
		place_name_by_place_id: dict[uuid.UUID, str] = {row[0]: row[1] for row in place_names_result.all()}

		linked_pair_result = await self._session.execute(
			select(ProjectPlace.place_id).where(
				ProjectPlace.project_id == payload.project_id,
				ProjectPlace.place_id.in_(payload.place_ids),
			)
		)
		found_place_ids = set(linked_pair_result.scalars().all())
		missing_places = set(payload.place_ids) - found_place_ids
		if missing_places:
			raise HTTPException(
				status_code=status.HTTP_400_BAD_REQUEST,
				detail="One or more places are not linked to the selected project.",
			)

		existing_result = await self._session.execute(
			select(
				AuditorAssignment.auditor_profile_id,
				AuditorAssignment.place_id,
			).where(
				AuditorAssignment.project_id == payload.project_id,
				AuditorAssignment.auditor_profile_id.in_(payload.auditor_profile_ids),
				AuditorAssignment.place_id.in_(payload.place_ids),
			)
		)
		existing_pairs: set[tuple[uuid.UUID, uuid.UUID | None]] = {(row[0], row[1]) for row in existing_result.all()}

		created_count = 0
		for auditor_id in payload.auditor_profile_ids:
			for place_id in payload.place_ids:
				if (auditor_id, place_id) in existing_pairs:
					continue

				assignment = AuditorAssignment(
					auditor_profile_id=auditor_id,
					project_id=payload.project_id,
					place_id=place_id,
				)
				self._session.add(assignment)
				await self._session.flush()
				created_count += 1

				notify_user_id = user_id_by_profile_id.get(auditor_id)
				place_name = place_name_by_place_id.get(place_id)
				if notify_user_id is not None and place_name is not None:
					try:
						await NotificationService.create_assignment_notification(
							db=self._session,
							user_id=notify_user_id,
							assignment_id=assignment.id,
							place_name=place_name,
						)
						logger.info(
							"Created assignment notification",
							extra={
								"assignment_id": str(assignment.id),
								"auditor_profile_id": str(auditor_id),
								"user_id": str(notify_user_id),
							},
						)
					except Exception as exc:
						logger.error(
							"Failed to create assignment notification",
							extra={
								"assignment_id": str(assignment.id),
								"auditor_profile_id": str(auditor_id),
								"error": str(exc),
							},
						)
						raise

		if created_count > 0:
			try:
				await self._session.commit()
			except IntegrityError:
				await self._session.rollback()
				raise HTTPException(
					status_code=status.HTTP_409_CONFLICT,
					detail="One or more assignments already exist.",
				)

		return created_count

	async def update_assignment(
		self,
		*,
		actor: CurrentUserContext,
		auditor_profile_id: uuid.UUID,
		assignment_id: uuid.UUID,
		payload: AssignmentWriteRequest,
	) -> AssignmentResponse:
		"""Update an existing assignment scope."""

		require_manager_or_admin_user(actor)
		await self._get_auditor_profile(auditor_profile_id=auditor_profile_id)
		await self._validate_assignment_scope(payload=payload)
		await self._ensure_actor_can_manage_scope(
			actor=actor,
			project_id=payload.project_id,
			place_id=payload.place_id,
		)

		assignment = await self._get_assignment(
			assignment_id=assignment_id,
			auditor_profile_id=auditor_profile_id,
		)
		await self._ensure_assignment_scope_is_unique(
			auditor_profile_id=auditor_profile_id,
			project_id=payload.project_id,
			place_id=payload.place_id,
			exclude_assignment_id=assignment.id,
		)
		assignment.project_id = payload.project_id
		assignment.place_id = payload.place_id
		try:
			await self._commit_and_refresh(assignment)
		except IntegrityError as error:
			await self._session.rollback()
			self._raise_if_duplicate_assignment_integrity_error(error)
			raise
		hydrated_assignment = await self._get_assignment_with_scope(
			assignment_id=assignment.id,
			auditor_profile_id=auditor_profile_id,
		)
		return self._serialize_assignment(hydrated_assignment)

	async def delete_assignment(
		self,
		*,
		actor: CurrentUserContext,
		auditor_profile_id: uuid.UUID,
		assignment_id: uuid.UUID,
	) -> None:
		"""Delete one assignment row under an auditor profile."""

		require_manager_or_admin_user(actor)
		await self._get_auditor_profile(auditor_profile_id=auditor_profile_id)
		assignment = await self._get_assignment(
			assignment_id=assignment_id,
			auditor_profile_id=auditor_profile_id,
		)
		await self._ensure_actor_can_manage_scope(
			actor=actor,
			project_id=assignment.project_id,
			place_id=assignment.place_id,
		)
		await self._session.delete(assignment)
		await self._session.commit()

	async def _get_auditor_profile(self, *, auditor_profile_id: uuid.UUID) -> AuditorProfile:
		"""Load an auditor profile and fail with 404 when it does not exist."""

		result = await self._session.execute(select(AuditorProfile).where(AuditorProfile.id == auditor_profile_id))
		profile = result.scalar_one_or_none()
		if profile is None:
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail="Auditor profile not found.",
			)
		return profile

	async def _get_assignment(
		self,
		*,
		assignment_id: uuid.UUID,
		auditor_profile_id: uuid.UUID,
	) -> AuditorAssignment:
		"""Load an assignment under one profile and fail with 404 when missing."""

		result = await self._session.execute(
			select(AuditorAssignment).where(
				AuditorAssignment.id == assignment_id,
				AuditorAssignment.auditor_profile_id == auditor_profile_id,
			)
		)
		assignment = result.scalar_one_or_none()
		if assignment is None:
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail="Assignment not found.",
			)
		return assignment

	async def _get_assignment_with_scope(
		self,
		*,
		assignment_id: uuid.UUID,
		auditor_profile_id: uuid.UUID,
	) -> AuditorAssignment:
		"""Load an assignment together with its project/place display relationships."""

		result = await self._session.execute(
			select(AuditorAssignment)
			.where(
				AuditorAssignment.id == assignment_id,
				AuditorAssignment.auditor_profile_id == auditor_profile_id,
			)
			.options(
				selectinload(AuditorAssignment.project),
				selectinload(AuditorAssignment.place),
			)
		)
		assignment = result.scalar_one_or_none()
		if assignment is None:
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail="Assignment not found.",
			)
		return assignment

	async def _validate_assignment_scope(self, *, payload: AssignmentWriteRequest) -> None:
		"""Ensure the request targets a real project–place pair linked in ``project_places``."""

		project_result = await self._session.execute(select(Project.id).where(Project.id == payload.project_id))
		if project_result.scalar_one_or_none() is None:
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail="Project not found.",
			)

		place_result = await self._session.execute(select(Place.id).where(Place.id == payload.place_id))
		if place_result.scalar_one_or_none() is None:
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail="Place not found.",
			)

		pair_result = await self._session.execute(
			select(ProjectPlace.project_id).where(
				ProjectPlace.project_id == payload.project_id,
				ProjectPlace.place_id == payload.place_id,
			)
		)
		if pair_result.scalar_one_or_none() is None:
			raise HTTPException(
				status_code=status.HTTP_400_BAD_REQUEST,
				detail="The selected place is not linked to the selected project.",
			)

	def _ensure_profile_access(self, *, actor: CurrentUserContext, profile: AuditorProfile) -> None:
		"""Allow managers or the owning auditor account to read assignment rows."""

		if actor.role is CurrentUserRole.ADMIN:
			return
		if actor.role is CurrentUserRole.MANAGER:
			if actor.account_id is None:
				raise HTTPException(
					status_code=status.HTTP_403_FORBIDDEN,
					detail="Manager account context is required for assignment access.",
				)
			return
		if actor.role is CurrentUserRole.AUDITOR and actor.account_id == profile.account_id:
			return
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail="You do not have permission to access this auditor profile.",
		)

	async def _ensure_actor_can_manage_scope(
		self,
		*,
		actor: CurrentUserContext,
		project_id: uuid.UUID | None,
		place_id: uuid.UUID | None,
	) -> None:
		"""Ensure manager actors can only manage assignments inside their own project scope."""

		if actor.role is CurrentUserRole.ADMIN:
			return
		if actor.role is not CurrentUserRole.MANAGER or actor.account_id is None:
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail="Manager account context is required for assignment management.",
			)

		scope_account_id = await self._resolve_scope_account_id(project_id=project_id)
		if scope_account_id != actor.account_id:
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail="Managers can only manage assignments inside their own account.",
			)

	async def _ensure_assignment_scope_is_unique(
		self,
		*,
		auditor_profile_id: uuid.UUID,
		project_id: uuid.UUID,
		place_id: uuid.UUID,
		exclude_assignment_id: uuid.UUID | None = None,
	) -> None:
		"""Reject duplicate assignment scopes before hitting DB constraints."""

		query = select(AuditorAssignment.id).where(
			AuditorAssignment.auditor_profile_id == auditor_profile_id,
			AuditorAssignment.project_id == project_id,
			AuditorAssignment.place_id == place_id,
		)
		if exclude_assignment_id is not None:
			query = query.where(AuditorAssignment.id != exclude_assignment_id)

		result = await self._session.execute(query.limit(1))
		if result.scalar_one_or_none() is None:
			return

		raise HTTPException(
			status_code=status.HTTP_409_CONFLICT,
			detail="An assignment already exists for this auditor and scope.",
		)

	async def _resolve_scope_account_id(
		self,
		*,
		project_id: uuid.UUID | None,
	) -> uuid.UUID:
		"""Resolve the owning account id for one assignment scope."""

		if project_id is None:
			raise HTTPException(
				status_code=status.HTTP_400_BAD_REQUEST,
				detail="Assignment scope is required.",
			)

		project_account_result = await self._session.execute(select(Project.account_id).where(Project.id == project_id))
		project_account_id = project_account_result.scalar_one_or_none()
		if project_account_id is None:
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail="Project not found.",
			)
		return project_account_id

	@staticmethod
	def _raise_if_duplicate_assignment_integrity_error(error: IntegrityError) -> None:
		"""Convert known duplicate-scope assignment integrity errors into HTTP 409s."""

		raw_error_message = str(getattr(error, "orig", error))
		sqlstate = getattr(getattr(error, "orig", None), "sqlstate", None)
		if sqlstate == "23505" and "uq_auditor_assignments_auditor_project_place" in raw_error_message:
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail="An assignment already exists for this auditor and scope.",
			) from error

	def _serialize_assignment(self, assignment: AuditorAssignment) -> AssignmentResponse:
		"""Convert an ORM assignment row into the API response model."""

		project_name = assignment.project.name if assignment.project is not None else "Unknown project"
		return AssignmentResponse(
			id=assignment.id,
			auditor_profile_id=assignment.auditor_profile_id,
			project_id=assignment.project_id,
			place_id=assignment.place_id,
			scope_type="place",
			scope_id=assignment.place.id,
			scope_name=assignment.place.name,
			project_name=project_name,
			place_name=assignment.place.name,
			assigned_at=assignment.assigned_at,
		)
