from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.errors import AuthenticationError
from app.core.security import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    password_problems,
    verify_password,
)
from tests.helpers import TEST_SECRET, make_settings


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("correct horse 42")
    assert hashed.startswith("$argon2id$")
    assert verify_password("correct horse 42", hashed)
    assert not verify_password("wrong horse 42", hashed)


def test_verify_password_with_malformed_hash_is_false() -> None:
    assert verify_password("anything", "not-a-hash") is False


@pytest.mark.parametrize(
    ("password", "expected_problem"),
    [
        ("short1", "at least"),
        ("onlyletters", "digit"),
        ("1234567890", "letter"),
        (" padded password 1", "whitespace"),
    ],
)
def test_password_problems(password: str, expected_problem: str) -> None:
    assert any(expected_problem in p for p in password_problems(password, 10))


def test_strong_password_has_no_problems() -> None:
    assert password_problems("a-strong-passw0rd", 10) == []


def test_access_token_roundtrip() -> None:
    settings = make_settings()
    user_id, org_id = uuid.uuid4(), uuid.uuid4()
    token, expires_in = create_access_token(user_id=user_id, organization_id=org_id, role="admin", settings=settings)
    claims = decode_access_token(token, settings)
    assert claims.user_id == user_id
    assert claims.organization_id == org_id
    assert claims.role == "admin"
    assert expires_in == settings.access_token_ttl_minutes * 60


def test_expired_access_token_rejected() -> None:
    settings = make_settings()
    token, _ = create_access_token(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        role="user",
        settings=settings,
        now=datetime.now(UTC) - timedelta(hours=2),
    )
    with pytest.raises(AuthenticationError) as exc:
        decode_access_token(token, settings)
    assert exc.value.code == "token_expired"


def test_token_with_wrong_audience_rejected() -> None:
    settings = make_settings()
    token, _ = create_access_token(user_id=uuid.uuid4(), organization_id=uuid.uuid4(), role="user", settings=settings)
    other = make_settings(jwt_audience="another-api")
    with pytest.raises(AuthenticationError):
        decode_access_token(token, other)


def test_token_signed_with_other_key_rejected() -> None:
    settings = make_settings()
    forged = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "org": str(uuid.uuid4()),
            "role": "admin",
            "typ": "access",
            "jti": "x",
            "iat": 0,
            "exp": 9999999999,
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
        },
        "some-other-secret-that-is-long-enough-123",
        algorithm="HS256",
    )
    with pytest.raises(AuthenticationError):
        decode_access_token(forged, settings)


def test_non_access_token_type_rejected() -> None:
    settings = make_settings()
    now = int(datetime.now(UTC).timestamp())
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "org": str(uuid.uuid4()),
            "role": "admin",
            "typ": "refresh",
            "jti": "x",
            "iat": now,
            "exp": now + 60,
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
        },
        TEST_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(AuthenticationError):
        decode_access_token(token, settings)


def test_alg_none_rejected() -> None:
    settings = make_settings()
    now = int(datetime.now(UTC).timestamp())
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "org": str(uuid.uuid4()),
            "role": "admin",
            "typ": "access",
            "jti": "x",
            "iat": now,
            "exp": now + 60,
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
        },
        None,
        algorithm="none",
    )
    with pytest.raises(AuthenticationError):
        decode_access_token(token, settings)


def test_refresh_tokens_are_random_and_hashed() -> None:
    a, b = generate_refresh_token(), generate_refresh_token()
    assert a != b and len(a) >= 60
    assert hash_token(a) == hash_token(a) and len(hash_token(a)) == 64
    assert hash_token(a) != hash_token(b)


def test_short_jwt_secret_rejected() -> None:
    with pytest.raises(ValueError, match="at least 32"):
        make_settings(jwt_secret_key="too-short")


def test_production_requires_secure_cookies() -> None:
    with pytest.raises(ValueError, match="COOKIE_SECURE"):
        make_settings(environment="production", cookie_secure=False)
