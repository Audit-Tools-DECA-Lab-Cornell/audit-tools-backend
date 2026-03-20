"""
Playspace instrument and execution enums.

Response models for the full instrument have been removed. The instrument
definition now lives as a static TypeScript constant in the mobile frontend.
"""

from __future__ import annotations

from enum import Enum


class ExecutionMode(str, Enum):
    """High-level form scope selected by a place participant."""

    AUDIT = "audit"
    SURVEY = "survey"
    BOTH = "both"


class AssignmentRole(str, Enum):
    """Place-scoped capabilities enabled for one assignment."""

    AUDITOR = "auditor"
    PLACE_ADMIN = "place_admin"


class ConstructKey(str, Enum):
    """Construct bucket used for totals and exports."""

    USABILITY = "usability"
    PLAY_VALUE = "play_value"


class ScaleKey(str, Enum):
    """Supported scoring columns in the PVUA workbook."""

    QUANTITY = "quantity"
    DIVERSITY = "diversity"
    SOCIABILITY = "sociability"
    CHALLENGE = "challenge"


class PreAuditInputType(str, Enum):
    """Supported mobile inputs for pre-audit prompts."""

    SINGLE_SELECT = "single_select"
    MULTI_SELECT = "multi_select"
    AUTO_TIMESTAMP = "auto_timestamp"
