"""
Audit execution endpoints for Playspace.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.core.actors import CurrentUserContext
from app.products.playspace.routes.dependencies import (
    AUDIT_SERVICE_DEPENDENCY,
    CURRENT_USER_DEPENDENCY,
)
from app.products.playspace.schemas import (
    AuditDraftPatchRequest,
    AuditDraftSaveResponse,
    AuditSessionResponse,
    PlaceAuditAccessRequest,
)
from app.products.playspace.services import PlayspaceAuditService

######################################################################################
################################# Audit Endpoints ####################################
######################################################################################

router = APIRouter(tags=["playspace"])


@router.post("/places/{place_id}/audits/access")
async def create_or_resume_place_audit(
    place_id: uuid.UUID,
    payload: PlaceAuditAccessRequest,
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceAuditService = AUDIT_SERVICE_DEPENDENCY,
) -> AuditSessionResponse:
    """Create or return the current auditor's audit for a project-place pair."""

    return await service.create_or_resume_audit(
        actor=current_user,
        place_id=place_id,
        payload=payload,
    )


@router.get("/audits/{audit_id}")
async def get_audit_session(
    audit_id: uuid.UUID,
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceAuditService = AUDIT_SERVICE_DEPENDENCY,
) -> AuditSessionResponse:
    """Return the current state of a playspace audit draft or submission."""

    return await service.get_audit_session(actor=current_user, audit_id=audit_id)


@router.patch("/audits/{audit_id}/draft")
async def patch_audit_draft(
    audit_id: uuid.UUID,
    payload: AuditDraftPatchRequest,
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceAuditService = AUDIT_SERVICE_DEPENDENCY,
) -> AuditDraftSaveResponse:
    """Save a typed draft patch to an existing playspace audit."""

    return await service.patch_audit_draft(
        actor=current_user,
        audit_id=audit_id,
        payload=payload,
    )


@router.patch("/places/{place_id}/audits/draft")
async def patch_place_draft(
    place_id: uuid.UUID,
    payload: AuditDraftPatchRequest,
    project_id: uuid.UUID = Query(...),
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceAuditService = AUDIT_SERVICE_DEPENDENCY,
) -> AuditDraftSaveResponse:
    """Compatibility draft save endpoint keyed by project-place pair instead of audit id."""

    return await service.patch_place_draft(
        actor=current_user,
        place_id=place_id,
        project_id=project_id,
        payload=payload,
    )


@router.post("/audits/{audit_id}/submit")
async def submit_audit(
    audit_id: uuid.UUID,
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceAuditService = AUDIT_SERVICE_DEPENDENCY,
) -> AuditSessionResponse:
    """Submit a playspace audit after validating completion and calculating scores."""

    return await service.submit_audit(actor=current_user, audit_id=audit_id)
