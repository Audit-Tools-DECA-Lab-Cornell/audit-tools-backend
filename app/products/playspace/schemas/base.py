"""
Shared Playspace schema primitives and base model configuration.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

######################################################################################
#################################### Base Schemas ####################################
######################################################################################

JsonDict = dict[str, object]
ProjectStatus = Literal["planned", "active", "completed"]
PlaceActivityStatus = Literal["not_started", "in_progress", "submitted"]


class ApiModel(BaseModel):
    """Immutable product-specific response model."""

    model_config = ConfigDict(from_attributes=True, frozen=True)


class RequestModel(BaseModel):
    """Strict request model used by playspace-specific endpoints."""

    model_config = ConfigDict(extra="forbid")
