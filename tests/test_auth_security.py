from __future__ import annotations

from app.auth_security import (
	generate_access_token,
	generate_password_reset_token,
	verify_access_token,
	verify_password_reset_token,
)


def test_access_token_round_trip(monkeypatch) -> None:
	monkeypatch.setenv("AUTH_TOKEN_SECRET_KEY", "test-secret")
	token, _ = generate_access_token("123e4567-e89b-12d3-a456-426614174000")

	assert verify_access_token(token) == "123e4567-e89b-12d3-a456-426614174000"


def test_access_token_rejects_tampering(monkeypatch) -> None:
	monkeypatch.setenv("AUTH_TOKEN_SECRET_KEY", "test-secret")
	token, _ = generate_access_token("123e4567-e89b-12d3-a456-426614174000")
	tampered = token.replace("session.", "session.x", 1)

	assert verify_access_token(tampered) is None


def test_password_reset_token_round_trip(monkeypatch) -> None:
	monkeypatch.setenv("AUTH_TOKEN_SECRET_KEY", "test-secret")
	token, _ = generate_password_reset_token(
		"123e4567-e89b-12d3-a456-426614174000",
		"pbkdf2_sha256$390000$salt$digest",
	)

	user_id, password_fingerprint = verify_password_reset_token(token) or ("", "")
	assert user_id == "123e4567-e89b-12d3-a456-426614174000"
	assert len(password_fingerprint) == 24


def test_password_reset_token_rejects_tampering(monkeypatch) -> None:
	monkeypatch.setenv("AUTH_TOKEN_SECRET_KEY", "test-secret")
	token, _ = generate_password_reset_token(
		"123e4567-e89b-12d3-a456-426614174000",
		"pbkdf2_sha256$390000$salt$digest",
	)
	tampered = token.replace("reset.", "reset.x", 1)

	assert verify_password_reset_token(tampered) is None
