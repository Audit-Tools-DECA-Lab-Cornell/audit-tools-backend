"""Map persisted execution_mode strings to audit vs survey coverage (including `both`)."""

from __future__ import annotations

from app.products.playspace.schemas.instrument import ExecutionMode


def _parse_execution_mode(raw: str | None) -> ExecutionMode | None:
	if raw is None or not str(raw).strip():
		return None
	try:
		return ExecutionMode(str(raw).strip())
	except ValueError:
		return None


def execution_mode_includes_audit(raw: str | None) -> bool:
	"""True when the mode is `audit` or `both` (submission contributes to the audit partition)."""

	mode = _parse_execution_mode(raw)
	if mode is None:
		return False
	return mode in (ExecutionMode.AUDIT, ExecutionMode.BOTH)


def execution_mode_includes_survey(raw: str | None) -> bool:
	"""True when the mode is `survey` or `both` (submission contributes to the survey partition)."""

	mode = _parse_execution_mode(raw)
	if mode is None:
		return False
	return mode in (ExecutionMode.SURVEY, ExecutionMode.BOTH)
