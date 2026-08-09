from __future__ import annotations

from typing import TypedDict


class AuditContextExportFields(TypedDict):
	place_size: str | None
	current_users_0_5: str | None
	current_users_6_12: str | None
	current_users_13_17: str | None
	current_users_18_plus: str | None
	weather_conditions: list[str]


def extract_audit_context_export_fields(responses_json: object) -> AuditContextExportFields:
	pre_audit: dict[object, object] = {}
	if isinstance(responses_json, dict):
		candidate = responses_json.get("pre_audit")
		if isinstance(candidate, dict):
			pre_audit = candidate

	def read_optional_string(key: str) -> str | None:
		value = pre_audit.get(key)
		return value if isinstance(value, str) and value.strip() else None

	weather = pre_audit.get("weather_conditions")
	weather_conditions = (
		[value for value in weather if isinstance(value, str) and value.strip()] if isinstance(weather, list) else []
	)

	return {
		"place_size": read_optional_string("place_size"),
		"current_users_0_5": read_optional_string("current_users_0_5"),
		"current_users_6_12": read_optional_string("current_users_6_12"),
		"current_users_13_17": read_optional_string("current_users_13_17"),
		"current_users_18_plus": read_optional_string("current_users_18_plus"),
		"weather_conditions": weather_conditions,
	}
