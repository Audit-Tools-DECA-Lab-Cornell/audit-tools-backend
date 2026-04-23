"""Administrator read service for global Playspace oversight dashboards."""

from __future__ import annotations

import math
from datetime import datetime, timezone

from sqlalchemy import and_, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actors import CurrentUserContext, require_admin_user
from app.models import (
	Account,
	AccountType,
	Audit,
	AuditorAssignment,
	AuditorProfile,
	AuditStatus,
	Place,
	Project,
	ProjectPlace,
)
from app.products.playspace.instrument import (
	INSTRUMENT_KEY,
	INSTRUMENT_NAME,
	INSTRUMENT_VERSION,
	get_canonical_instrument_payload,
)
from app.products.playspace.schemas import PaginatedResponse
from app.products.playspace.schemas.admin import (
	AdminAccountRowResponse,
	AdminAuditorRowResponse,
	AdminAuditRowResponse,
	AdminOverviewResponse,
	AdminPlaceRowResponse,
	AdminProjectRowResponse,
	AdminSystemResponse,
)
from app.products.playspace.services.instrument import get_active_instrument
from app.products.playspace.services.privacy import mask_email

DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 100


def _round_score(value: float | None) -> float | None:
	"""Round a numeric score when present."""

	if value is None:
		return None
	return round(value, 1)


def _total_pages(total_count: int, page_size: int) -> int:
	"""Return a stable page count for paginated responses."""

	if total_count <= 0:
		return 1
	return max(1, math.ceil(total_count / page_size))


class PlayspaceAdminService:
	"""Global read service for the full administrator dashboard."""

	def __init__(self, session: AsyncSession):
		self._session = session

	@staticmethod
	def _require_admin(actor: CurrentUserContext) -> None:
		"""Guard admin-only endpoints."""

		require_admin_user(actor)

	async def get_overview(self, *, actor: CurrentUserContext) -> AdminOverviewResponse:
		"""Return global overview counters."""

		self._require_admin(actor)

		total_accounts_result = await self._session.execute(select(func.count(Account.id)))
		total_projects_result = await self._session.execute(select(func.count(Project.id)))
		total_places_result = await self._session.execute(select(func.count(Place.id)))
		total_auditors_result = await self._session.execute(select(func.count(AuditorProfile.id)))
		audit_counts_result = await self._session.execute(
			select(
				func.count(Audit.id).label("total_audits"),
				func.count(Audit.id).filter(Audit.status == AuditStatus.SUBMITTED).label("submitted_audits"),
				func.count(Audit.id)
				.filter(Audit.status.in_([AuditStatus.IN_PROGRESS, AuditStatus.PAUSED]))
				.label("in_progress_audits"),
			)
		)
		audit_counts = audit_counts_result.one()

		return AdminOverviewResponse(
			total_accounts=int(total_accounts_result.scalar_one() or 0),
			total_projects=int(total_projects_result.scalar_one() or 0),
			total_places=int(total_places_result.scalar_one() or 0),
			total_auditors=int(total_auditors_result.scalar_one() or 0),
			total_audits=int(audit_counts.total_audits or 0),
			submitted_audits=int(audit_counts.submitted_audits or 0),
			in_progress_audits=int(audit_counts.in_progress_audits or 0),
		)

	async def list_accounts(
		self,
		*,
		actor: CurrentUserContext,
		page: int = 1,
		page_size: int = DEFAULT_PAGE_SIZE,
		search: str | None = None,
		sort: str | None = None,
		account_types: list[str] | None = None,
	) -> PaginatedResponse[AdminAccountRowResponse]:
		"""Return paginated global account rows."""

		self._require_admin(actor)

		normalized_search = search.strip() if search is not None and search.strip() else None
		normalized_account_types: list[AccountType] = []
		for raw_value in account_types or []:
			try:
				normalized_account_types.append(AccountType(raw_value))
			except ValueError:
				continue

		safe_page_size = max(1, min(page_size, MAX_PAGE_SIZE))
		offset = max(page - 1, 0) * safe_page_size

		projects_count_subquery = (
			select(
				Project.account_id.label("account_id"),
				func.count(Project.id).label("projects_count"),
			)
			.group_by(Project.account_id)
			.subquery()
		)
		places_count_subquery = (
			select(
				Project.account_id.label("account_id"),
				func.count(ProjectPlace.place_id).label("places_count"),
			)
			.select_from(Project)
			.outerjoin(ProjectPlace, ProjectPlace.project_id == Project.id)
			.group_by(Project.account_id)
			.subquery()
		)
		auditors_count_subquery = (
			select(
				AuditorProfile.account_id.label("account_id"),
				func.count(AuditorProfile.id).label("auditors_count"),
			)
			.group_by(AuditorProfile.account_id)
			.subquery()
		)

		filtered_rows_query = (
			select(
				Account.id.label("account_id"),
				Account.name.label("name"),
				Account.account_type.label("account_type"),
				Account.email.label("email"),
				Account.created_at.label("created_at"),
				projects_count_subquery.c.projects_count.label("projects_count"),
				places_count_subquery.c.places_count.label("places_count"),
				auditors_count_subquery.c.auditors_count.label("auditors_count"),
			)
			.select_from(Account)
			.outerjoin(
				projects_count_subquery,
				projects_count_subquery.c.account_id == Account.id,
			)
			.outerjoin(
				places_count_subquery,
				places_count_subquery.c.account_id == Account.id,
			)
			.outerjoin(
				auditors_count_subquery,
				auditors_count_subquery.c.account_id == Account.id,
			)
		)

		if normalized_search is not None:
			search_term = f"%{normalized_search}%"
			filtered_rows_query = filtered_rows_query.where(
				or_(Account.name.ilike(search_term), Account.email.ilike(search_term))
			)

		if normalized_account_types:
			filtered_rows_query = filtered_rows_query.where(Account.account_type.in_(normalized_account_types))

		filtered_rows_subquery = filtered_rows_query.subquery()
		total_count_result = await self._session.execute(select(func.count()).select_from(filtered_rows_subquery))
		total_count = int(total_count_result.scalar_one() or 0)

		raw_sort = sort.strip() if sort is not None and sort.strip() else "-created_at"
		is_descending = raw_sort.startswith("-")
		sort_key = raw_sort[1:] if is_descending else raw_sort
		sort_map = {
			"name": filtered_rows_subquery.c.name,
			"account_type": filtered_rows_subquery.c.account_type,
			"projects_count": filtered_rows_subquery.c.projects_count,
			"places_count": filtered_rows_subquery.c.places_count,
			"auditors_count": filtered_rows_subquery.c.auditors_count,
			"created_at": filtered_rows_subquery.c.created_at,
		}
		sort_column = sort_map.get(sort_key, filtered_rows_subquery.c.created_at)
		primary_order = sort_column.desc().nulls_last() if is_descending else sort_column.asc().nulls_last()

		rows_result = await self._session.execute(
			select(filtered_rows_subquery)
			.order_by(
				primary_order,
				filtered_rows_subquery.c.name.asc(),
				filtered_rows_subquery.c.account_id.asc(),
			)
			.offset(offset)
			.limit(safe_page_size)
		)

		return PaginatedResponse[AdminAccountRowResponse](
			items=[
				AdminAccountRowResponse(
					account_id=row.account_id,
					name=row.name,
					account_type=row.account_type,
					email_masked=mask_email(row.email),
					created_at=row.created_at,
					projects_count=int(row.projects_count or 0),
					places_count=int(row.places_count or 0),
					auditors_count=int(row.auditors_count or 0),
				)
				for row in rows_result.all()
			],
			total_count=total_count,
			page=page,
			page_size=safe_page_size,
			total_pages=_total_pages(total_count, safe_page_size),
		)

	async def list_projects(
		self,
		*,
		actor: CurrentUserContext,
		page: int = 1,
		page_size: int = DEFAULT_PAGE_SIZE,
		search: str | None = None,
		sort: str | None = None,
	) -> PaginatedResponse[AdminProjectRowResponse]:
		"""Return paginated global project rows."""

		self._require_admin(actor)

		normalized_search = search.strip() if search is not None and search.strip() else None
		safe_page_size = max(1, min(page_size, MAX_PAGE_SIZE))
		offset = max(page - 1, 0) * safe_page_size

		places_count_subquery = (
			select(
				ProjectPlace.project_id.label("project_id"),
				func.count(ProjectPlace.place_id).label("places_count"),
			)
			.group_by(ProjectPlace.project_id)
			.subquery()
		)
		project_assignment_scope = (
			select(
				AuditorAssignment.project_id.label("project_id"),
				AuditorAssignment.auditor_profile_id.label("auditor_profile_id"),
			)
			.where(AuditorAssignment.project_id.is_not(None))
			.subquery()
		)
		auditors_count_subquery = (
			select(
				project_assignment_scope.c.project_id.label("project_id"),
				func.count(distinct(project_assignment_scope.c.auditor_profile_id)).label("auditors_count"),
			)
			.group_by(project_assignment_scope.c.project_id)
			.subquery()
		)
		audit_stats_subquery = (
			select(
				Audit.project_id.label("project_id"),
				func.count(Audit.id).filter(Audit.status == AuditStatus.SUBMITTED).label("audits_completed"),
				func.avg(Audit.summary_score)
				.filter(
					and_(
						Audit.status == AuditStatus.SUBMITTED,
						Audit.summary_score.is_not(None),
					)
				)
				.label("average_score"),
			)
			.select_from(Audit)
			.group_by(Audit.project_id)
			.subquery()
		)

		filtered_rows_query = (
			select(
				Project.id.label("project_id"),
				Project.account_id.label("account_id"),
				Account.name.label("account_name"),
				Project.name.label("name"),
				Project.start_date.label("start_date"),
				Project.end_date.label("end_date"),
				places_count_subquery.c.places_count.label("places_count"),
				auditors_count_subquery.c.auditors_count.label("auditors_count"),
				audit_stats_subquery.c.audits_completed.label("audits_completed"),
				audit_stats_subquery.c.average_score.label("average_score"),
			)
			.select_from(Project)
			.join(Account, Project.account_id == Account.id)
			.outerjoin(
				places_count_subquery,
				places_count_subquery.c.project_id == Project.id,
			)
			.outerjoin(
				auditors_count_subquery,
				auditors_count_subquery.c.project_id == Project.id,
			)
			.outerjoin(
				audit_stats_subquery,
				audit_stats_subquery.c.project_id == Project.id,
			)
		)

		if normalized_search is not None:
			search_term = f"%{normalized_search}%"
			filtered_rows_query = filtered_rows_query.where(
				or_(Project.name.ilike(search_term), Account.name.ilike(search_term))
			)

		filtered_rows_subquery = filtered_rows_query.subquery()
		total_count_result = await self._session.execute(select(func.count()).select_from(filtered_rows_subquery))
		total_count = int(total_count_result.scalar_one() or 0)

		raw_sort = sort.strip() if sort is not None and sort.strip() else "-date_range"
		is_descending = raw_sort.startswith("-")
		sort_key = raw_sort[1:] if is_descending else raw_sort
		sort_map = {
			"project": filtered_rows_subquery.c.name,
			"name": filtered_rows_subquery.c.name,
			"date_range": func.coalesce(
				filtered_rows_subquery.c.start_date,
				filtered_rows_subquery.c.end_date,
			),
			"places_count": filtered_rows_subquery.c.places_count,
			"auditors_count": filtered_rows_subquery.c.auditors_count,
			"audits_completed": filtered_rows_subquery.c.audits_completed,
			"average_score": filtered_rows_subquery.c.average_score,
		}
		sort_column = sort_map.get(sort_key, sort_map["date_range"])
		primary_order = sort_column.desc().nulls_last() if is_descending else sort_column.asc().nulls_last()

		rows_result = await self._session.execute(
			select(filtered_rows_subquery)
			.order_by(
				primary_order,
				filtered_rows_subquery.c.name.asc(),
				filtered_rows_subquery.c.project_id.asc(),
			)
			.offset(offset)
			.limit(safe_page_size)
		)

		return PaginatedResponse[AdminProjectRowResponse](
			items=[
				AdminProjectRowResponse(
					project_id=row.project_id,
					account_id=row.account_id,
					account_name=row.account_name,
					name=row.name,
					start_date=row.start_date,
					end_date=row.end_date,
					places_count=int(row.places_count or 0),
					auditors_count=int(row.auditors_count or 0),
					audits_completed=int(row.audits_completed or 0),
					average_score=_round_score(float(row.average_score) if row.average_score is not None else None),
				)
				for row in rows_result.all()
			],
			total_count=total_count,
			page=page,
			page_size=safe_page_size,
			total_pages=_total_pages(total_count, safe_page_size),
		)

	async def list_places(
		self,
		*,
		actor: CurrentUserContext,
		page: int = 1,
		page_size: int = DEFAULT_PAGE_SIZE,
		search: str | None = None,
		sort: str | None = None,
	) -> PaginatedResponse[AdminPlaceRowResponse]:
		"""Return paginated global place rows."""

		self._require_admin(actor)

		normalized_search = search.strip() if search is not None and search.strip() else None
		safe_page_size = max(1, min(page_size, MAX_PAGE_SIZE))
		offset = max(page - 1, 0) * safe_page_size

		place_audit_summary_subquery = (
			select(
				Audit.project_id.label("project_id"),
				Audit.place_id.label("place_id"),
				func.count(Audit.id).filter(Audit.status == AuditStatus.SUBMITTED).label("audits_completed"),
				func.avg(Audit.summary_score)
				.filter(
					and_(
						Audit.status == AuditStatus.SUBMITTED,
						Audit.summary_score.is_not(None),
					)
				)
				.label("average_score"),
				func.max(Audit.submitted_at)
				.filter(
					and_(
						Audit.status == AuditStatus.SUBMITTED,
						Audit.submitted_at.is_not(None),
					)
				)
				.label("last_audited_at"),
			)
			.group_by(Audit.project_id, Audit.place_id)
			.subquery()
		)

		filtered_rows_query = (
			select(
				ProjectPlace.place_id.label("place_id"),
				Project.id.label("project_id"),
				Project.name.label("project_name"),
				Account.id.label("account_id"),
				Account.name.label("account_name"),
				Place.name.label("name"),
				Place.city.label("city"),
				Place.province.label("province"),
				Place.country.label("country"),
				place_audit_summary_subquery.c.audits_completed.label("audits_completed"),
				place_audit_summary_subquery.c.average_score.label("average_score"),
				place_audit_summary_subquery.c.last_audited_at.label("last_audited_at"),
			)
			.select_from(ProjectPlace)
			.join(Project, ProjectPlace.project_id == Project.id)
			.join(Account, Project.account_id == Account.id)
			.join(Place, ProjectPlace.place_id == Place.id)
			.outerjoin(
				place_audit_summary_subquery,
				and_(
					place_audit_summary_subquery.c.project_id == Project.id,
					place_audit_summary_subquery.c.place_id == Place.id,
				),
			)
		)

		if normalized_search is not None:
			search_term = f"%{normalized_search}%"
			filtered_rows_query = filtered_rows_query.where(
				or_(
					Place.name.ilike(search_term),
					Project.name.ilike(search_term),
					Account.name.ilike(search_term),
					Place.city.ilike(search_term),
					Place.province.ilike(search_term),
					Place.country.ilike(search_term),
				)
			)

		filtered_rows_subquery = filtered_rows_query.subquery()
		total_count_result = await self._session.execute(select(func.count()).select_from(filtered_rows_subquery))
		total_count = int(total_count_result.scalar_one() or 0)

		raw_sort = sort.strip() if sort is not None and sort.strip() else "-last_audited_at"
		is_descending = raw_sort.startswith("-")
		sort_key = raw_sort[1:] if is_descending else raw_sort
		sort_map = {
			"place": filtered_rows_subquery.c.name,
			"name": filtered_rows_subquery.c.name,
			"audits_completed": filtered_rows_subquery.c.audits_completed,
			"average_score": filtered_rows_subquery.c.average_score,
			"last_audited_at": filtered_rows_subquery.c.last_audited_at,
		}
		sort_column = sort_map.get(sort_key, filtered_rows_subquery.c.last_audited_at)
		primary_order = sort_column.desc().nulls_last() if is_descending else sort_column.asc().nulls_last()

		rows_result = await self._session.execute(
			select(filtered_rows_subquery)
			.order_by(
				primary_order,
				filtered_rows_subquery.c.name.asc(),
				filtered_rows_subquery.c.place_id.asc(),
			)
			.offset(offset)
			.limit(safe_page_size)
		)

		return PaginatedResponse[AdminPlaceRowResponse](
			items=[
				AdminPlaceRowResponse(
					place_id=row.place_id,
					project_id=row.project_id,
					project_name=row.project_name,
					account_id=row.account_id,
					account_name=row.account_name,
					name=row.name,
					city=row.city,
					province=row.province,
					country=row.country,
					audits_completed=int(row.audits_completed or 0),
					average_score=_round_score(float(row.average_score) if row.average_score is not None else None),
					last_audited_at=row.last_audited_at,
				)
				for row in rows_result.all()
			],
			total_count=total_count,
			page=page,
			page_size=safe_page_size,
			total_pages=_total_pages(total_count, safe_page_size),
		)

	async def list_auditors(
		self,
		*,
		actor: CurrentUserContext,
		page: int = 1,
		page_size: int = DEFAULT_PAGE_SIZE,
		search: str | None = None,
		sort: str | None = None,
	) -> PaginatedResponse[AdminAuditorRowResponse]:
		"""Return paginated global auditor rows."""

		self._require_admin(actor)

		normalized_search = search.strip() if search is not None and search.strip() else None
		safe_page_size = max(1, min(page_size, MAX_PAGE_SIZE))
		offset = max(page - 1, 0) * safe_page_size

		assignment_counts_subquery = (
			select(
				AuditorAssignment.auditor_profile_id.label("auditor_profile_id"),
				func.count(AuditorAssignment.id).label("assignments_count"),
			)
			.group_by(AuditorAssignment.auditor_profile_id)
			.subquery()
		)
		audit_stats_subquery = (
			select(
				Audit.auditor_profile_id.label("auditor_profile_id"),
				func.count(Audit.id).filter(Audit.status == AuditStatus.SUBMITTED).label("completed_audits"),
				func.max(func.coalesce(Audit.submitted_at, Audit.started_at)).label("last_active_at"),
			)
			.group_by(Audit.auditor_profile_id)
			.subquery()
		)

		filtered_rows_query = (
			select(
				AuditorProfile.id.label("auditor_profile_id"),
				AuditorProfile.account_id.label("account_id"),
				AuditorProfile.auditor_code.label("auditor_code"),
				AuditorProfile.email.label("email"),
				assignment_counts_subquery.c.assignments_count.label("assignments_count"),
				audit_stats_subquery.c.completed_audits.label("completed_audits"),
				audit_stats_subquery.c.last_active_at.label("last_active_at"),
			)
			.select_from(AuditorProfile)
			.outerjoin(
				assignment_counts_subquery,
				assignment_counts_subquery.c.auditor_profile_id == AuditorProfile.id,
			)
			.outerjoin(
				audit_stats_subquery,
				audit_stats_subquery.c.auditor_profile_id == AuditorProfile.id,
			)
		)

		if normalized_search is not None:
			search_term = f"%{normalized_search}%"
			filtered_rows_query = filtered_rows_query.where(
				or_(
					AuditorProfile.auditor_code.ilike(search_term),
					AuditorProfile.email.ilike(search_term),
				)
			)

		filtered_rows_subquery = filtered_rows_query.subquery()
		total_count_result = await self._session.execute(select(func.count()).select_from(filtered_rows_subquery))
		total_count = int(total_count_result.scalar_one() or 0)

		raw_sort = sort.strip() if sort is not None and sort.strip() else "-last_active_at"
		is_descending = raw_sort.startswith("-")
		sort_key = raw_sort[1:] if is_descending else raw_sort
		sort_map = {
			"auditor": filtered_rows_subquery.c.auditor_code,
			"auditor_code": filtered_rows_subquery.c.auditor_code,
			"assignments_count": filtered_rows_subquery.c.assignments_count,
			"completed_audits": filtered_rows_subquery.c.completed_audits,
			"last_active_at": filtered_rows_subquery.c.last_active_at,
		}
		sort_column = sort_map.get(sort_key, filtered_rows_subquery.c.last_active_at)
		primary_order = sort_column.desc().nulls_last() if is_descending else sort_column.asc().nulls_last()

		rows_result = await self._session.execute(
			select(filtered_rows_subquery)
			.order_by(
				primary_order,
				filtered_rows_subquery.c.auditor_code.asc(),
				filtered_rows_subquery.c.auditor_profile_id.asc(),
			)
			.offset(offset)
			.limit(safe_page_size)
		)

		return PaginatedResponse[AdminAuditorRowResponse](
			items=[
				AdminAuditorRowResponse(
					auditor_profile_id=row.auditor_profile_id,
					account_id=row.account_id,
					auditor_code=row.auditor_code,
					email_masked=mask_email(row.email),
					assignments_count=int(row.assignments_count or 0),
					completed_audits=int(row.completed_audits or 0),
					last_active_at=row.last_active_at,
				)
				for row in rows_result.all()
			],
			total_count=total_count,
			page=page,
			page_size=safe_page_size,
			total_pages=_total_pages(total_count, safe_page_size),
		)

	async def list_audits(
		self,
		*,
		actor: CurrentUserContext,
		page: int = 1,
		page_size: int = DEFAULT_PAGE_SIZE,
		search: str | None = None,
		sort: str | None = None,
		statuses: list[str] | None = None,
	) -> PaginatedResponse[AdminAuditRowResponse]:
		"""Return paginated global audit rows."""

		self._require_admin(actor)

		normalized_search = search.strip() if search is not None and search.strip() else None
		normalized_statuses = {
			raw_value for raw_value in (statuses or []) if raw_value in {"IN_PROGRESS", "PAUSED", "SUBMITTED"}
		}
		safe_page_size = max(1, min(page_size, MAX_PAGE_SIZE))
		offset = max(page - 1, 0) * safe_page_size

		filtered_rows_query = (
			select(
				Audit.id.label("audit_id"),
				Audit.audit_code.label("audit_code"),
				Audit.status.label("status"),
				Account.id.label("account_id"),
				Account.name.label("account_name"),
				Project.id.label("project_id"),
				Project.name.label("project_name"),
				Place.id.label("place_id"),
				Place.name.label("place_name"),
				AuditorProfile.auditor_code.label("auditor_code"),
				Audit.started_at.label("started_at"),
				Audit.submitted_at.label("submitted_at"),
				Audit.summary_score.label("summary_score"),
			)
			.select_from(Audit)
			.join(Place, Audit.place_id == Place.id)
			.join(Project, Audit.project_id == Project.id)
			.join(Account, Project.account_id == Account.id)
			.join(AuditorProfile, Audit.auditor_profile_id == AuditorProfile.id)
		)

		if normalized_search is not None:
			search_term = f"%{normalized_search}%"
			filtered_rows_query = filtered_rows_query.where(
				or_(
					Audit.audit_code.ilike(search_term),
					AuditorProfile.auditor_code.ilike(search_term),
					Place.name.ilike(search_term),
					Project.name.ilike(search_term),
					Account.name.ilike(search_term),
				)
			)

		if normalized_statuses:
			filtered_rows_query = filtered_rows_query.where(Audit.status.in_(normalized_statuses))

		filtered_rows_subquery = filtered_rows_query.subquery()
		total_count_result = await self._session.execute(select(func.count()).select_from(filtered_rows_subquery))
		total_count = int(total_count_result.scalar_one() or 0)

		raw_sort = sort.strip() if sort is not None and sort.strip() else "-submitted_at"
		is_descending = raw_sort.startswith("-")
		sort_key = raw_sort[1:] if is_descending else raw_sort
		sort_map = {
			"audit_code": filtered_rows_subquery.c.audit_code,
			"status": filtered_rows_subquery.c.status,
			"started_at": filtered_rows_subquery.c.started_at,
			"submitted_at": filtered_rows_subquery.c.submitted_at,
			"summary_score": filtered_rows_subquery.c.summary_score,
		}
		sort_column = sort_map.get(sort_key, filtered_rows_subquery.c.submitted_at)
		primary_order = sort_column.desc().nulls_last() if is_descending else sort_column.asc().nulls_last()

		rows_result = await self._session.execute(
			select(filtered_rows_subquery)
			.order_by(
				primary_order,
				filtered_rows_subquery.c.started_at.desc(),
				filtered_rows_subquery.c.audit_id.desc(),
			)
			.offset(offset)
			.limit(safe_page_size)
		)

		return PaginatedResponse[AdminAuditRowResponse](
			items=[
				AdminAuditRowResponse(
					audit_id=row.audit_id,
					audit_code=row.audit_code,
					status=row.status,
					account_id=row.account_id,
					account_name=row.account_name,
					project_id=row.project_id,
					project_name=row.project_name,
					place_id=row.place_id,
					place_name=row.place_name,
					auditor_code=row.auditor_code,
					started_at=row.started_at,
					submitted_at=row.submitted_at,
					summary_score=_round_score(float(row.summary_score) if row.summary_score is not None else None),
				)
				for row in rows_result.all()
			],
			total_count=total_count,
			page=page,
			page_size=safe_page_size,
			total_pages=_total_pages(total_count, safe_page_size),
		)

	async def get_system(self, *, actor: CurrentUserContext) -> AdminSystemResponse:
		"""Return system metadata with the active instrument payload."""

		self._require_admin(actor)

		db_instrument = await get_active_instrument(self._session, INSTRUMENT_KEY)

		if db_instrument is not None:
			instrument_content = db_instrument.content
			instrument_version = db_instrument.instrument_version
		else:
			instrument_content = {"en": get_canonical_instrument_payload()}
			instrument_version = INSTRUMENT_VERSION

		return AdminSystemResponse(
			instrument_key=INSTRUMENT_KEY,
			instrument_name=INSTRUMENT_NAME,
			instrument_version=instrument_version,
			generated_at=datetime.now(timezone.utc),
			instrument=instrument_content,
		)
