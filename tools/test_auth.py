#!/usr/bin/env python3
"""
Test the Flask session-based auth system.
Tests:
- /auth/roblox/callback success sets cookie and redirects to /?auth=success
- Missing code, state mismatch, Roblox failure give right 4xx/5xx
- /auth/me works with the cookie
- Logout clears the cookie
- /health returns 200
- API-key routes return 503
"""
import os
import sys
import json
import time
from unittest.mock import patch, MagicMock

# Set required env vars before importing app
os.environ["SESSION_SECRET"] = "test-secret-key-for-testing-only"
os.environ["ROBLOX_OAUTH_CLIENT_ID"] = "test-client-id"
os.environ["ROBLOX_OAUTH_CLIENT_SECRET"] = "test-client-secret"
os.environ["ROBLOX_OAUTH_REDIRECT_URI"] = "http://localhost/auth/roblox/callback"
os.environ["SUPABASE_URL"] = "https://test.supabase.co"
os.environ["SUPABASE_KEY"] = "test-key"

# Add backend to path
sys.path.insert(0, "/root/3DRBX-MT/backend")

from main import app


def test_health():
    """Test /health returns 200 with maintenance state."""
    with app.test_client() as client:
        with patch("main.get_maintenance_state", return_value=False):
            resp = client.get("/health")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "ok"
            assert "maintenance" in data
            assert "cookie_set" in data
    print("✅ /health returns 200")


def test_auth_roblox_sets_state_cookie():
    """Test /auth/roblox sets oauth_state cookie and redirects."""
    with app.test_client() as client:
        resp = client.get("/auth/roblox")
        assert resp.status_code == 302
        assert resp.location.startswith("https://apis.roblox.com/oauth/v1/authorize?")
        # Check oauth_state cookie is set
        cookie = resp.headers.get("Set-Cookie", "")
        assert "oauth_state=" in cookie
        assert "HttpOnly" in cookie
        assert "Secure" in cookie
        assert "SameSite=Lax" in cookie
    print("✅ /auth/roblox sets oauth_state cookie and redirects")


def test_callback_missing_code():
    """Test callback with missing code returns 400."""
    with app.test_client() as client:
        # First set the state cookie
        client.set_cookie("oauth_state", "test-state")
        resp = client.get("/auth/roblox/callback?state=test-state")
        assert resp.status_code == 400
        assert b"Missing authorization code" in resp.data
    print("✅ Callback with missing code returns 400")


def test_callback_state_mismatch():
    """Test callback with state mismatch returns 400."""
    with app.test_client() as client:
        # Set a different state cookie
        client.set_cookie("oauth_state", "different-state")
        resp = client.get("/auth/roblox/callback?code=test-code&state=test-state")
        assert resp.status_code == 400
        assert b"Invalid OAuth state" in resp.data
    print("✅ Callback with state mismatch returns 400")


def test_callback_oauth_error():
    """Test callback with OAuth error returns 400."""
    with app.test_client() as client:
        client.set_cookie("oauth_state", "test-state")
        resp = client.get("/auth/roblox/callback?state=test-state&error=access_denied")
        assert resp.status_code == 400
        assert b"OAuth error" in resp.data
    print("✅ Callback with OAuth error returns 400")


def test_callback_success():
    """Test successful callback sets session and redirects."""
    with app.test_client() as client:
        client.set_cookie("oauth_state", "test-state")

        # Mock the token and userinfo requests
        with patch("main.requests.post") as mock_post, \
             patch("main.requests.get") as mock_get:

            # Mock token response
            mock_token_resp = MagicMock()
            mock_token_resp.raise_for_status = MagicMock()
            mock_token_resp.json.return_value = {"access_token": "test-access-token"}
            mock_post.return_value = mock_token_resp

            # Mock userinfo response
            mock_user_resp = MagicMock()
            mock_user_resp.raise_for_status = MagicMock()
            mock_user_resp.json.return_value = {
                "sub": "123456",
                "preferred_username": "testuser",
                "name": "Test User",
                "picture": "https://example.com/avatar.png"
            }
            mock_get.return_value = mock_user_resp

            resp = client.get("/auth/roblox/callback?code=test-code&state=test-state")

            assert resp.status_code == 302
            assert resp.location == "/?auth=success"

            # Check session cookie is set (may be multiple Set-Cookie headers)
            cookies = resp.headers.getlist("Set-Cookie")
            session_cookie_found = any("session=" in c for c in cookies)
            assert session_cookie_found

            # Check oauth_state cookie is cleared
            oauth_cleared = any("oauth_state=;" in c or "oauth_state=; Expires" in c for c in cookies)
            assert oauth_cleared
    print("✅ Successful callback sets session and redirects")


def test_auth_me_with_session():
    """Test /auth/me returns user info when session exists."""
    with app.test_client() as client:
        # Set up a session
        with client.session_transaction() as sess:
            sess["user"] = {
                "user_id": "123456",
                "username": "testuser",
                "display_name": "Test User",
                "avatar_url": "https://example.com/avatar.png",
                "expiry": time.time() + 3600
            }

        resp = client.get("/auth/me")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["authenticated"] is True
        assert data["via"] == "session"
        assert data["user_id"] == "123456"
        assert data["username"] == "testuser"
    print("✅ /auth/me works with session cookie")


def test_auth_me_without_session():
    """Test /auth/me returns authenticated: false without session."""
    with app.test_client() as client:
        resp = client.get("/auth/me")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["authenticated"] is False
    print("✅ /auth/me returns authenticated: false without session")


def test_auth_me_expired_session():
    """Test /auth/me returns authenticated: false with expired session."""
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user"] = {
                "user_id": "123456",
                "username": "testuser",
                "display_name": "Test User",
                "avatar_url": "https://example.com/avatar.png",
                "expiry": time.time() - 100  # expired
            }

        resp = client.get("/auth/me")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["authenticated"] is False
    print("✅ /auth/me returns authenticated: false with expired session")


def test_logout_clears_session():
    """Test /auth/logout clears the session."""
    with app.test_client() as client:
        # Set up a session
        with client.session_transaction() as sess:
            sess["user"] = {
                "user_id": "123456",
                "username": "testuser",
                "expiry": time.time() + 3600
            }

        # Verify session exists
        resp = client.get("/auth/me")
        assert resp.get_json()["authenticated"] is True

        # Logout
        resp = client.post("/auth/logout")
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

        # Verify session is cleared
        resp = client.get("/auth/me")
        assert resp.get_json()["authenticated"] is False
    print("✅ /auth/logout clears session")


def test_api_key_routes_return_503():
    """Test API key routes return 503."""
    with app.test_client() as client:
        # create_api_key
        resp = client.post("/api/admin/keys", json={"scopes": ["catalog"]})
        assert resp.status_code == 503
        assert resp.get_json()["error_code"] == "api_keys_unavailable"

        # list_api_keys
        resp = client.get("/api/admin/keys")
        assert resp.status_code == 503
        assert resp.get_json()["error_code"] == "api_keys_unavailable"

        # revoke_api_key
        resp = client.delete("/api/admin/keys/test-key")
        assert resp.status_code == 503
        assert resp.get_json()["error_code"] == "api_keys_unavailable"
    print("✅ API key routes return 503")


if __name__ == "__main__":
    test_health()
    test_auth_roblox_sets_state_cookie()
    test_callback_missing_code()
    test_callback_state_mismatch()
    test_callback_oauth_error()
    test_callback_success()
    test_auth_me_with_session()
    test_auth_me_without_session()
    test_auth_me_expired_session()
    test_logout_clears_session()
    test_api_key_routes_return_503()
    print("\n✅ All auth tests passed!")