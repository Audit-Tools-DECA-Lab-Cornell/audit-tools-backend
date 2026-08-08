from app.products.playspace.services.export_context import extract_audit_context_export_fields


def test_extract_audit_context_export_fields_preserves_requested_context() -> None:
	assert extract_audit_context_export_fields(
		{
			"pre_audit": {
				"place_size": "large",
				"current_users_0_5": "none",
				"current_users_6_12": "a_few",
				"current_users_13_17": "a_lot",
				"current_users_18_plus": "a_few",
				"weather_conditions": ["sunshine", "light_rain"],
			}
		}
	) == {
		"place_size": "large",
		"current_users_0_5": "none",
		"current_users_6_12": "a_few",
		"current_users_13_17": "a_lot",
		"current_users_18_plus": "a_few",
		"weather_conditions": ["sunshine", "light_rain"],
	}


def test_extract_audit_context_export_fields_normalizes_missing_or_invalid_values() -> None:
	assert extract_audit_context_export_fields({"pre_audit": {"place_size": "", "weather_conditions": "sunshine"}}) == {
		"place_size": None,
		"current_users_0_5": None,
		"current_users_6_12": None,
		"current_users_13_17": None,
		"current_users_18_plus": None,
		"weather_conditions": [],
	}
