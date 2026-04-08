from __future__ import annotations

from app.auth_security import generate_access_token, verify_access_token


def test_access_token_round_trip(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_TOKEN_SECRET_KEY", "test-secret")
    token, _ = generate_access_token("123e4567-e89b-12d3-a456-426614174000")

    assert verify_access_token(token) == "123e4567-e89b-12d3-a456-426614174000"


def test_access_token_rejects_tampering(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_TOKEN_SECRET_KEY", "test-secret")
    token, _ = generate_access_token("123e4567-e89b-12d3-a456-426614174000")
    tampered = token.replace("session.", "session.x", 1)

    assert verify_access_token(tampered) is None
