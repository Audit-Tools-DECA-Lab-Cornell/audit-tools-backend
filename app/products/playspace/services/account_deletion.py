"""
Self-service account deletion for Playspace managers and auditors.

The governing rule is **delete the person, preserve the work**. Submitted audits
belong to the organisation that commissioned them: reports, exports, and score
history must keep resolving long after the individual who collected the data has
gone. So deletion removes the login and every personal detail, while the audit
record survives under a scrubbed placeholder.

Two structural hazards drive most of the code here:

* ``AuditorProfile`` is the anchor every submitted audit points at, and its
  ``audits``/``playspace_submissions`` relationships are ``delete-orphan`` with
  matching ``ON DELETE CASCADE`` foreign keys. Deleting - or ORM-detaching - that
  profile would take the preserved audits with it. The profile is therefore
  *never* deleted and *never* touched through ``Account.auditor_profiles``; it is
  rewritten in place with a direct ``UPDATE``.
* A self-registered auditor owns a personal ``AccountType.AUDITOR`` account keyed
  by their email. Leaving it behind would permanently reserve that address, so it
  is removed once it is proven to hold nothing else. Organisation
  (``AccountType.MANAGER``) accounts are always preserved.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth_security import verify_password
from app.models import (
	Account,
	AccountType,
	Audit,
	AuditorAccessRequest,
	AuditorAssignment,
	AuditorProfile,
	AuditStatus,
	BugReport,
	ManagerInvite,
	ManagerProfile,
	PlayspaceSubmission,
	Project,
	User,
)
from app.products.playspace.schemas.me import AccountDeletionBlocker

DELETED_AUDITOR_DISPLAY_NAME = "Deleted auditor"


@dataclass(frozen=True)
class AccountDeletionPreview:
	"""Counts and eligibility backing the deletion confirmation screen."""

	role: str
	submitted_audits_preserved: int
	draft_audits_to_delete: int
	active_assignments_to_delete: int
	pending_submissions: int
	is_primary_manager: bool
	can_delete: bool
	blocker: AccountDeletionBlocker | None


class PlayspaceAccountDeletionService:
	"""Preview, execute, and unblock self-service deletion of the current user."""

	def __init__(self, *, session: AsyncSession) -> None:
		self._session = session

	######################################################################################
	############################### Shared Lookups #######################################
	######################################################################################

	async def _load_deletable_user(self, *, user_id: uuid.UUID, lock: bool) -> User:
		"""Load the acting user, rejecting administrators.

		Administrators are excluded on purpose: an admin login is platform
		infrastructure rather than a personal account, and removing one through a
		self-service path could strand the platform with no operator.
		"""

		query = select(User).where(User.id == user_id)
		if lock:
			query = query.with_for_update()
		result = await self._session.execute(query)
		user = result.scalar_one_or_none()
		if user is None:
			raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

		if user.account_type == AccountType.ADMIN:
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail="Administrator accounts cannot be deleted from self-service settings.",
			)
		return user

	async def _load_auditor_profile(self, *, user_id: uuid.UUID, lock: bool) -> AuditorProfile:
		query = select(AuditorProfile).where(AuditorProfile.user_id == user_id)
		if lock:
			query = query.with_for_update()
		result = await self._session.execute(query)
		profile = result.scalar_one_or_none()
		if profile is None:
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail="Auditor profile not found for this user.",
			)
		return profile

	async def _load_manager_profile(self, *, user_id: uuid.UUID, lock: bool) -> ManagerProfile:
		query = select(ManagerProfile).where(ManagerProfile.user_id == user_id)
		if lock:
			query = query.with_for_update()
		result = await self._session.execute(query)
		profile = result.scalar_one_or_none()
		if profile is None:
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail="Manager profile not found for this user.",
			)
		return profile

	async def _count(self, query) -> int:  # type: ignore[no-untyped-def]
		result = await self._session.execute(query)
		return int(result.scalar_one() or 0)

	######################################################################################
	############################### Preview ##############################################
	######################################################################################

	async def preview(self, *, user_id: uuid.UUID) -> AccountDeletionPreview:
		"""Summarise what deletion would preserve and remove for this user."""

		user = await self._load_deletable_user(user_id=user_id, lock=False)
		if user.account_type == AccountType.MANAGER:
			return await self._preview_manager(user=user)
		return await self._preview_auditor(user=user)

	async def _preview_auditor(self, *, user: User) -> AccountDeletionPreview:
		profile = await self._load_auditor_profile(user_id=user.id, lock=False)

		submitted = await self._count(
			select(func.count())
			.select_from(PlayspaceSubmission)
			.where(
				PlayspaceSubmission.auditor_profile_id == profile.id,
				PlayspaceSubmission.status == AuditStatus.SUBMITTED,
			)
		)
		submitted += await self._count(
			select(func.count())
			.select_from(Audit)
			.where(Audit.auditor_profile_id == profile.id, Audit.status == AuditStatus.SUBMITTED)
		)

		drafts = await self._count(
			select(func.count())
			.select_from(PlayspaceSubmission)
			.where(
				PlayspaceSubmission.auditor_profile_id == profile.id,
				PlayspaceSubmission.status != AuditStatus.SUBMITTED,
			)
		)
		drafts += await self._count(
			select(func.count())
			.select_from(Audit)
			.where(Audit.auditor_profile_id == profile.id, Audit.status != AuditStatus.SUBMITTED)
		)

		assignments = await self._count(
			select(func.count())
			.select_from(AuditorAssignment)
			.where(AuditorAssignment.auditor_profile_id == profile.id)
		)

		pending = await self._pending_submission_count(auditor_profile_id=profile.id)

		blocker: AccountDeletionBlocker | None = None
		if pending > 0:
			blocker = AccountDeletionBlocker.PENDING_SUBMISSION_DELIVERY
		elif await self._personal_account_has_dependencies(user=user):
			blocker = AccountDeletionBlocker.PERSONAL_ACCOUNT_HAS_DEPENDENCIES

		return AccountDeletionPreview(
			role="AUDITOR",
			submitted_audits_preserved=submitted,
			draft_audits_to_delete=drafts,
			active_assignments_to_delete=assignments,
			pending_submissions=pending,
			is_primary_manager=False,
			can_delete=blocker is None,
			blocker=blocker,
		)

	async def _preview_manager(self, *, user: User) -> AccountDeletionPreview:
		profile = await self._load_manager_profile(user_id=user.id, lock=False)

		# A manager collects no audits personally. The count shown to them is the
		# organisation's submitted work, because that is what their confirmation
		# screen is reassuring them about: leaving does not take it away.
		submitted = 0
		if profile.account_id is not None:
			submitted = await self._count(
				select(func.count())
				.select_from(PlayspaceSubmission)
				.join(Project, Project.id == PlayspaceSubmission.project_id)
				.where(
					Project.account_id == profile.account_id,
					PlayspaceSubmission.status == AuditStatus.SUBMITTED,
				)
			)

		blocker = AccountDeletionBlocker.PRIMARY_MANAGER_TRANSFER_REQUIRED if profile.is_primary else None

		return AccountDeletionPreview(
			role="MANAGER",
			submitted_audits_preserved=submitted,
			draft_audits_to_delete=0,
			active_assignments_to_delete=0,
			pending_submissions=0,
			is_primary_manager=profile.is_primary,
			can_delete=blocker is None,
			blocker=blocker,
		)

	async def _pending_submission_count(self, *, auditor_profile_id: uuid.UUID) -> int:
		"""Count audits the auditor has tried to submit that have not landed yet.

		``submit_intended_at`` is the offline submit beacon. A row carrying it
		while still not ``SUBMITTED`` means finished work is mid-delivery, and
		deleting the auditor now would destroy it.
		"""

		return await self._count(
			select(func.count())
			.select_from(PlayspaceSubmission)
			.where(
				PlayspaceSubmission.auditor_profile_id == auditor_profile_id,
				PlayspaceSubmission.status != AuditStatus.SUBMITTED,
				PlayspaceSubmission.submit_intended_at.is_not(None),
			)
		)

	async def _personal_account_has_dependencies(self, *, user: User) -> bool:
		"""Report whether this auditor's personal account holds anything but them.

		A personal ``AuditorProfile`` account is expected to contain exactly one
		user and one profile. Anything else means the data does not match the
		shape this deletion path is safe for, so the caller blocks instead of
		guessing which rows may be removed.
		"""

		account_id = user.account_id
		if account_id is None:
			return False

		account_result = await self._session.execute(select(Account).where(Account.id == account_id))
		account = account_result.scalar_one_or_none()
		if account is None or account.account_type != AccountType.AUDITOR:
			return False

		other_users = await self._count(
			select(func.count()).select_from(User).where(User.account_id == account_id, User.id != user.id)
		)
		other_managers = await self._count(
			select(func.count()).select_from(ManagerProfile).where(ManagerProfile.account_id == account_id)
		)
		other_auditors = await self._count(
			select(func.count())
			.select_from(AuditorProfile)
			.where(AuditorProfile.account_id == account_id, AuditorProfile.user_id != user.id)
		)
		owned_projects = await self._count(
			select(func.count()).select_from(Project).where(Project.account_id == account_id)
		)
		return (other_users + other_managers + other_auditors + owned_projects) > 0

	######################################################################################
	############################### Deletion #############################################
	######################################################################################

	async def delete_account(
		self,
		*,
		user_id: uuid.UUID,
		current_password: str,
	) -> None:
		"""Delete the authenticated user after verifying their password.

		The literal ``DELETE`` confirmation is enforced by the request schema, so
		by the time execution reaches here the person has typed it exactly.
		"""

		user = await self._load_deletable_user(user_id=user_id, lock=True)

		if not verify_password(current_password, user.password_hash):
			raise HTTPException(
				status_code=status.HTTP_400_BAD_REQUEST,
				detail="Current password is incorrect.",
			)

		if user.account_type == AccountType.MANAGER:
			await self._delete_manager(user=user)
		else:
			await self._delete_auditor(user=user)

		await self._session.commit()

	async def _delete_auditor(self, *, user: User) -> None:
		profile = await self._load_auditor_profile(user_id=user.id, lock=True)

		# Submit delivery and submit-intent recording take this same profile lock.
		# Re-checking while it is held makes deletion and in-flight delivery
		# mutually exclusive, including submissions started after the preview.
		if await self._pending_submission_count(auditor_profile_id=profile.id) > 0:
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail=AccountDeletionBlocker.PENDING_SUBMISSION_DELIVERY.value,
			)
		if await self._personal_account_has_dependencies(user=user):
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail=AccountDeletionBlocker.PERSONAL_ACCOUNT_HAS_DEPENDENCIES.value,
			)

		# Unfinished work is personal to the auditor and never appears in a report,
		# so it goes. Child rows follow through ON DELETE CASCADE.
		await self._session.execute(
			delete(PlayspaceSubmission).where(
				PlayspaceSubmission.auditor_profile_id == profile.id,
				PlayspaceSubmission.status != AuditStatus.SUBMITTED,
			)
		)
		await self._session.execute(
			delete(Audit).where(
				Audit.auditor_profile_id == profile.id,
				Audit.status != AuditStatus.SUBMITTED,
			)
		)
		await self._session.execute(delete(AuditorAssignment).where(AuditorAssignment.auditor_profile_id == profile.id))

		identity_emails = {value for value in (user.email, profile.email) if value}
		if identity_emails:
			await self._session.execute(
				delete(AuditorAccessRequest).where(AuditorAccessRequest.email.in_(identity_emails))
			)

		await self._scrub_bug_report_identity(user_id=user.id)

		account_id = user.account_id
		await self._write_auditor_tombstone(profile_id=profile.id)
		await self._session.execute(delete(User).where(User.id == user.id))
		await self._session.flush()
		await self._delete_personal_account_if_empty(account_id=account_id)

	async def _write_auditor_tombstone(self, *, profile_id: uuid.UUID) -> None:
		"""Strip the person out of the auditor profile, keeping the audit anchor.

		Deliberately a direct ``UPDATE``: mutating this row through
		``Account.auditor_profiles`` would make SQLAlchemy treat it as an orphan
		and cascade the delete into every audit and submission it anchors.

		``auditor_code``, ``id``, and ``created_at`` survive so historical audit
		codes printed in past reports still resolve.
		"""

		await self._session.execute(
			update(AuditorProfile)
			.where(AuditorProfile.id == profile_id)
			.values(
				account_id=None,
				user_id=None,
				email=None,
				full_name=DELETED_AUDITOR_DISPLAY_NAME,
				phone=None,
				age_range=None,
				gender=None,
				city=None,
				province=None,
				country=None,
				role=None,
				terms_accepted_at=None,
			)
		)

	async def _delete_manager(self, *, user: User) -> None:
		profile = await self._load_manager_profile(user_id=user.id, lock=True)

		if profile.is_primary:
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail=AccountDeletionBlocker.PRIMARY_MANAGER_TRANSFER_REQUIRED.value,
			)

		await self._delete_manager_invites(user=user, profile=profile)
		await self._scrub_bug_report_identity(user_id=user.id)

		# The organisation account, its projects, places, and submitted audits all
		# stay. Projects this manager created keep existing with no creator, via
		# the ON DELETE SET NULL foreign key on projects.created_by_user_id.
		await self._session.execute(delete(ManagerProfile).where(ManagerProfile.id == profile.id))
		await self._session.execute(delete(User).where(User.id == user.id))

	async def _delete_manager_invites(self, *, user: User, profile: ManagerProfile) -> None:
		"""Invalidate invites created by or accepted by the departing manager.

		Invites are personal access tokens rather than organisation-owned work. A
		departing inviter's pending links must stop working, and the accepted invite
		that introduced a departing secondary manager must disappear from the
		primary manager's invite list. Matching the normalized identity email also
		cleans up older rows whose ``accepted_by_user_id`` was never populated.
		"""

		identity_emails = {
			value.strip().lower() for value in (user.email, profile.email) if value is not None and value.strip()
		}
		invite_matches = [
			ManagerInvite.invited_by_user_id == user.id,
			ManagerInvite.accepted_by_user_id == user.id,
		]
		if identity_emails:
			invite_matches.append(func.lower(ManagerInvite.email).in_(identity_emails))

		await self._session.execute(delete(ManagerInvite).where(or_(*invite_matches)))

	async def _scrub_bug_report_identity(self, *, user_id: uuid.UUID) -> None:
		"""Keep the bug report, drop the reporter.

		Reports stay triageable - description, diagnostics, screenshot, and status
		are untouched - but the reporter snapshot is cleared. ``reporter_email``
		and ``reporter_role`` are plain columns, so the foreign key alone would
		leave the person's address behind.
		"""

		await self._session.execute(
			update(BugReport)
			.where(BugReport.reporter_user_id == user_id)
			.values(reporter_user_id=None, reporter_email=None, reporter_role=None)
		)

	async def _delete_personal_account_if_empty(self, *, account_id: uuid.UUID | None) -> None:
		"""Remove a self-registered auditor's own account so their email frees up.

		Only ever an ``AccountType.AUDITOR`` row, and only once it is proven to
		hold no other users, profiles, or projects. Organisation accounts are
		preserved unconditionally.
		"""

		if account_id is None:
			return

		account_result = await self._session.execute(select(Account).where(Account.id == account_id))
		account = account_result.scalar_one_or_none()
		if account is None or account.account_type != AccountType.AUDITOR:
			return

		remaining_users = await self._count(select(func.count()).select_from(User).where(User.account_id == account_id))
		remaining_managers = await self._count(
			select(func.count()).select_from(ManagerProfile).where(ManagerProfile.account_id == account_id)
		)
		remaining_auditors = await self._count(
			select(func.count()).select_from(AuditorProfile).where(AuditorProfile.account_id == account_id)
		)
		remaining_projects = await self._count(
			select(func.count()).select_from(Project).where(Project.account_id == account_id)
		)
		if remaining_users or remaining_managers or remaining_auditors or remaining_projects:
			return

		await self._session.execute(delete(Account).where(Account.id == account_id))

	######################################################################################
	############################### Primary Transfer #####################################
	######################################################################################

	async def transfer_primary_manager(
		self,
		*,
		user_id: uuid.UUID,
		successor_manager_profile_id: uuid.UUID,
	) -> None:
		"""Hand the primary-manager role to another manager in the same organisation.

		Required before a primary manager can delete themselves: every
		organisation keeps exactly one accountable owner.
		"""

		user = await self._load_deletable_user(user_id=user_id, lock=True)
		if user.account_type != AccountType.MANAGER:
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail="Only managers can transfer organisation ownership.",
			)

		current = await self._load_manager_profile(user_id=user.id, lock=True)
		if not current.is_primary:
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail="Only the primary manager can transfer organisation ownership.",
			)

		successor_result = await self._session.execute(
			select(ManagerProfile).where(ManagerProfile.id == successor_manager_profile_id).with_for_update()
		)
		successor = successor_result.scalar_one_or_none()
		if successor is None or successor.account_id != current.account_id or successor.id == current.id:
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail="Successor manager not found in this organisation.",
			)
		if successor.user_id is None:
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail="The chosen manager has no active login and cannot take ownership.",
			)

		account_result = await self._session.execute(
			select(Account).where(Account.id == current.account_id).with_for_update()
		)
		account = account_result.scalar_one_or_none()
		if account is None:
			raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation not found.")

		# accounts.email is globally unique. Check before writing so a collision
		# surfaces as a clear conflict rather than a database error.
		successor_email = successor.email.strip().lower()
		conflict = await self._count(
			select(func.count())
			.select_from(Account)
			.where(func.lower(Account.email) == successor_email, Account.id != account.id)
		)
		if conflict:
			raise HTTPException(
				status_code=status.HTTP_409_CONFLICT,
				detail="That manager's email address is already used by another organisation.",
			)

		# The partial unique index allows one primary per account, so the outgoing
		# primary must be cleared and flushed before the incoming one is set.
		await self._session.execute(
			update(ManagerProfile).where(ManagerProfile.id == current.id).values(is_primary=False)
		)
		await self._session.flush()
		await self._session.execute(
			update(ManagerProfile).where(ManagerProfile.id == successor.id).values(is_primary=True)
		)
		await self._session.execute(update(Account).where(Account.id == account.id).values(email=successor_email))
		await self._session.commit()
