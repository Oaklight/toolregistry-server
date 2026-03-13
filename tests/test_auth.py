"""Tests for the authentication module."""

import pytest

from toolregistry_server.auth import (
    BearerTokenAuth,
    create_bearer_dependency,
    verify_token,
)

# ============== BearerTokenAuth Tests ==============


class TestBearerTokenAuth:
    """Tests for BearerTokenAuth class."""

    def test_init_with_tokens(self):
        """Test initialization with tokens."""
        auth = BearerTokenAuth(tokens=["token1", "token2"])
        assert auth.enabled
        assert "token1" in auth.tokens
        assert "token2" in auth.tokens

    def test_init_without_tokens(self):
        """Test initialization without tokens."""
        auth = BearerTokenAuth()
        assert not auth.enabled
        assert len(auth.tokens) == 0

    def test_init_with_empty_list(self):
        """Test initialization with empty list."""
        auth = BearerTokenAuth(tokens=[])
        assert not auth.enabled

    def test_verify_valid_token(self):
        """Test verifying a valid token."""
        auth = BearerTokenAuth(tokens=["secret"])
        assert auth.verify("secret")

    def test_verify_invalid_token(self):
        """Test verifying an invalid token."""
        auth = BearerTokenAuth(tokens=["secret"])
        assert not auth.verify("wrong")

    def test_verify_when_disabled(self):
        """Test that verification passes when auth is disabled."""
        auth = BearerTokenAuth()
        assert auth.verify("any-token")

    def test_add_token(self):
        """Test adding a token."""
        auth = BearerTokenAuth()
        assert not auth.enabled

        auth.add_token("new-token")
        assert auth.enabled
        assert auth.verify("new-token")

    def test_remove_token(self):
        """Test removing a token."""
        auth = BearerTokenAuth(tokens=["token1", "token2"])
        auth.remove_token("token1")

        assert not auth.verify("token1")
        assert auth.verify("token2")
        assert auth.enabled

    def test_remove_last_token_disables_auth(self):
        """Test that removing the last token disables auth."""
        auth = BearerTokenAuth(tokens=["only-token"])
        auth.remove_token("only-token")

        assert not auth.enabled

    def test_tokens_property_returns_copy(self):
        """Test that tokens property returns a copy."""
        auth = BearerTokenAuth(tokens=["token"])
        tokens = auth.tokens
        tokens.add("new")

        # Original should not be modified
        assert "new" not in auth.tokens


# ============== verify_token Tests ==============


class TestVerifyToken:
    """Tests for verify_token function."""

    def test_valid_token(self):
        """Test verifying a valid token."""
        assert verify_token("secret", ["secret", "other"])

    def test_invalid_token(self):
        """Test verifying an invalid token."""
        assert not verify_token("wrong", ["secret", "other"])

    def test_empty_valid_tokens(self):
        """Test with empty valid tokens list."""
        assert not verify_token("any", [])


# ============== create_bearer_dependency Tests ==============


class TestCreateBearerDependency:
    """Tests for create_bearer_dependency function."""

    def test_creates_dependency(self):
        """Test that dependency is created."""
        auth = BearerTokenAuth(tokens=["secret"])
        dep = create_bearer_dependency(auth)
        assert callable(dep)

    @pytest.mark.asyncio
    async def test_dependency_with_valid_token(self):
        """Test dependency with valid token."""
        from unittest.mock import MagicMock

        auth = BearerTokenAuth(tokens=["secret"])
        dep = create_bearer_dependency(auth)

        # Mock credentials
        credentials = MagicMock()
        credentials.credentials = "secret"

        result = await dep(credentials)
        assert result == "secret"

    @pytest.mark.asyncio
    async def test_dependency_with_invalid_token(self):
        """Test dependency with invalid token raises HTTPException."""
        from unittest.mock import MagicMock

        from fastapi import HTTPException

        auth = BearerTokenAuth(tokens=["secret"])
        dep = create_bearer_dependency(auth)

        # Mock credentials
        credentials = MagicMock()
        credentials.credentials = "wrong"

        with pytest.raises(HTTPException) as exc_info:
            await dep(credentials)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_dependency_when_auth_disabled(self):
        """Test dependency when auth is disabled."""
        from unittest.mock import MagicMock

        auth = BearerTokenAuth()  # No tokens = disabled
        dep = create_bearer_dependency(auth)

        # Mock credentials
        credentials = MagicMock()
        credentials.credentials = "any"

        result = await dep(credentials)
        assert result == ""


# ============== Integration Tests ==============


class TestAuthIntegration:
    """Integration tests for authentication with FastAPI."""

    def test_protected_endpoint(self):
        """Test that protected endpoint requires auth."""
        from fastapi import Depends, FastAPI
        from fastapi.testclient import TestClient

        auth = BearerTokenAuth(tokens=["secret-token"])
        dep = create_bearer_dependency(auth)

        app = FastAPI(dependencies=[Depends(dep)])

        @app.get("/protected")
        def protected():
            return {"message": "success"}

        client = TestClient(app)

        # Without token - HTTPBearer returns 401 or 403 depending on version
        response = client.get("/protected")
        assert response.status_code in (401, 403)

        # With invalid token
        response = client.get(
            "/protected",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert response.status_code == 401

        # With valid token
        response = client.get(
            "/protected",
            headers={"Authorization": "Bearer secret-token"},
        )
        assert response.status_code == 200
        assert response.json() == {"message": "success"}

    def test_unprotected_endpoint_when_disabled(self):
        """Test that endpoint is accessible when auth is disabled."""
        from fastapi import Depends, FastAPI
        from fastapi.testclient import TestClient

        auth = BearerTokenAuth()  # No tokens = disabled
        dep = create_bearer_dependency(auth)

        app = FastAPI(dependencies=[Depends(dep)])

        @app.get("/public")
        def public():
            return {"message": "public"}

        client = TestClient(app)

        # Should still require Bearer header format but accept any token
        response = client.get(
            "/public",
            headers={"Authorization": "Bearer any-token"},
        )
        assert response.status_code == 200
