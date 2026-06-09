"""Unit tests for auto-generated Playspace auditor codes."""

from __future__ import annotations

import re

from app.products.playspace.services.management import PlayspaceManagementService


_AUDITOR_CODE_PATTERN = re.compile(r"^AUD-[A-Z0-9]+-\d{2}-\d{8}$")


def _assert_valid_auditor_code(code: str) -> None:
	assert _AUDITOR_CODE_PATTERN.match(code) is not None


def test_generate_auditor_code_uses_word_initials_for_plain_names() -> None:
	code = PlayspaceManagementService._generate_auditor_code("Auckland Play Collective")

	_assert_valid_auditor_code(code)
	assert code.startswith("AUD-APC-")


def test_generate_auditor_code_handles_punctuation_without_changing_strategy() -> None:
	code = PlayspaceManagementService._generate_auditor_code("Auckland Play Collective Ltd.")

	_assert_valid_auditor_code(code)
	assert code.startswith("AUD-APCL-")


def test_generate_auditor_code_preserves_short_uppercase_acronyms() -> None:
	code = PlayspaceManagementService._generate_auditor_code("TM (TMW) Workshop")

	_assert_valid_auditor_code(code)
	assert code.startswith("AUD-TMTMWW-")


def test_generate_auditor_code_omits_brackets_and_punctuation() -> None:
	code = PlayspaceManagementService._generate_auditor_code("TM(TMW")

	_assert_valid_auditor_code(code)
	assert "(" not in code
	assert ")" not in code
	assert code.startswith("AUD-TMTMW-")


def test_generate_auditor_code_handles_ampersands_cleanly() -> None:
	code = PlayspaceManagementService._generate_auditor_code("R&D Labs")

	_assert_valid_auditor_code(code)
	assert code.startswith("AUD-RDL-")


def test_generate_auditor_code_preserves_short_alphanumeric_brand_tokens() -> None:
	code = PlayspaceManagementService._generate_auditor_code("3M Innovation Center")

	_assert_valid_auditor_code(code)
	assert code.startswith("AUD-3MIC-")


def test_generate_auditor_code_falls_back_when_name_has_no_alphanumeric() -> None:
	code = PlayspaceManagementService._generate_auditor_code("  ()  ")

	_assert_valid_auditor_code(code)
	assert code.startswith("AUD-ORG-")
