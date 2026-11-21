#!/usr/bin/env python3
"""
Comprehensive test suite for API Key requirements.
Covers all 8 self-review requirements with named tests.
Tests the API key helper functions and key management endpoints.
"""
import os
import sys
import json
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

# Set required env vars before importing main
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-service-key")
os.environ.setdefault("ROBLOX_OAUTH_CLIENT_ID", "test-client-id")
os.environ.setdefault("ROBLOX_OAUTH_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("ROBLOX_OAUTH_REDIRECT_URI", "https://test.com/auth/roblox/callback")
os.environ.setdefault("SESSION_SECRET", "test-session-secret-32-chars-minimum")
os.environ.setdefault("ROBLOX_API_KEY", "test-roblox-api-key")

# Mock supabase BEFORE importing main
mock_supabase_module = MagicMock()
mock_supabase_module.create_client = MagicMock(return_value=MagicMock())
sys.modules['supabase'] = mock_supabase_module
sys.modules['supabase.client'] = mock_supabase_module

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from main import app, hash_api_key, verify_api_key, generate_api_key, \
    API_KEY_PREFIX, API_KEY_PREFIX_LEN, API_KEY_RANDOM_BYTES, _VALID_SCOPES, \
    _validate_key_prefix, _validate_key_id, _validate_user_id, _validate_scopes, \
    require_api_key, dual_auth, require_session, jsonify

# Register test routes at module level (before any requests)
_test_routes_registered = False

def _register_test_routes():
    global _test_routes_registered
    if _test_routes_registered:
        return

    @app.route('/_test_require_api_key', methods=['GET'])
    @require_api_key()
    def _test_require_api_key_route():
        return jsonify({"ok": True, "auth": "require_api_key"})

    @app.route('/_test_require_api_key_admin', methods=['GET'])
    @require_api_key(["admin"])
    def _test_require_api_key_admin_route():
        return jsonify({"ok": True, "auth": "require_api_key_admin"})

    @app.route('/_test_dual_auth', methods=['GET'])
    @dual_auth()
    def _test_dual_auth_route():
        return jsonify({"ok": True, "auth": "dual_auth"})

    @app.route('/_test_require_api_key_qs', methods=['GET'])
    @require_api_key()
    def _test_require_api_key_qs_route():
        return jsonify({"ok": True, "auth": "require_api_key_qs"})

    @app.route('/_test_dual_auth_qs', methods=['GET'])
    @dual_auth()
    def _test_dual_auth_qs_route():
        return jsonify({"ok": True, "auth": "dual_auth_qs"})

    _test_routes_registered = True

# Call registration immediately
_register_test_routes()

import pytest


class BaseTest:
    """Base class with common test utilities."""

    def setup_method(self):
        self.app = app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

    def _mock_supabase_select(self, return_data=None, side_effect=None):
        """Create a mock Supabase chain for select().eq().is_().gt().execute()"""
        mock_chain = MagicMock()
        if side_effect:
            mock_chain.execute.side_effect = side_effect
        else:
            # Create a simple object with .data attribute (not a MagicMock)
            class MockResponse:
                def __init__(self, data):
                    self.data = data
            mock_chain.execute.return_value = MockResponse(return_data if return_data is not None else [])
        mock_chain.select.return_value = mock_chain
        mock_chain.eq.return_value = mock_chain
        mock_chain.is_.return_value = mock_chain
        mock_chain.gt.return_value = mock_chain
        return mock_chain

    def _make_failing_supabase(self):
        """Create mock that fails on any execute()"""
        mock_chain = MagicMock()
        mock_chain.execute.side_effect = Exception("Connection failed")
        mock_chain.eq.return_value = mock_chain
        mock_chain.is_.return_value = mock_chain
        mock_chain.gt.return_value = mock_chain
        mock_chain.order.return_value = mock_chain
        mock_chain.update.return_value = mock_chain
        mock_chain.insert.return_value = mock_chain
        mock_chain.select.return_value = mock_chain
        return mock_chain

    def _mock_supabase_for_create(self, active_count=0):
        """Create mock for create_api_key with given active key count."""
        class MockCountResponse:
            def __init__(self, count):
                self.count = count

        class MockInsertResponse:
            def __init__(self, data):
                self.data = data

        mock_count_chain = MagicMock()
        mock_count_chain.execute.return_value = MockCountResponse(active_count)
        mock_count_chain.select.return_value = mock_count_chain
        mock_count_chain.eq.return_value = mock_count_chain
        mock_count_chain.is_.return_value = mock_count_chain
        mock_count_chain.gt.return_value = mock_count_chain

        # Mock insert chain - insert() returns a chain that has execute()
        mock_insert_chain = MagicMock()
        mock_insert_chain.execute.return_value = MockInsertResponse([{
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "key_prefix": "abcdefgh",
            "name": "Test",
            "scopes": ["catalog"],
            "expires_at": "2026-12-31T23:59:59Z",
            "created_at": "2026-01-01T00:00:00Z",
            "rate_limit_rpm": 60,
            "rate_limit_rph": 1000,
        }])
        mock_insert_chain.insert.return_value = mock_insert_chain

        call_count = [0]
        def table_side_effect(name):
            if name == "api_keys":
                call_count[0] += 1
                if call_count[0] == 1:
                    return mock_count_chain
                return mock_insert_chain
            return MagicMock()

        return table_side_effect

    def _mock_supabase_list_keys(self, keys_data):
        """Mock for list_api_keys."""
        class MockListResponse:
            def __init__(self, data):
                self.data = data

        mock_chain = MagicMock()
        mock_chain.execute.return_value = MockListResponse(keys_data)
        mock_chain.select.return_value = mock_chain
        mock_chain.eq.return_value = mock_chain
        mock_chain.order.return_value = mock_chain
        return mock_chain

    def _mock_supabase_revoke(self, key_exists=True, key_user_id="12345"):
        """Mock for revoke_api_key."""
        class MockSelectResponse:
            def __init__(self, data):
                self.data = data

        class MockUpdateResponse:
            def __init__(self, data):
                self.data = data

        # First call: select to check ownership
        mock_select_chain = MagicMock()
        if key_exists:
            mock_select_chain.execute.return_value = MockSelectResponse([{"id": "550e8400-e29b-41d4-a716-446655440000", "user_id": key_user_id}])
        else:
            mock_select_chain.execute.return_value = MockSelectResponse([])
        mock_select_chain.select.return_value = mock_select_chain
        mock_select_chain.eq.return_value = mock_select_chain

        # Second call: update to revoke
        mock_update_chain = MagicMock()
        mock_update_chain.execute.return_value = MockUpdateResponse([{"revoked_at": "2026-01-01T00:00:00Z"}])
        mock_update_chain.update.return_value = mock_update_chain
        mock_update_chain.eq.return_value = mock_update_chain

        call_count = [0]
        def table_side_effect(name):
            if name == "api_keys":
                call_count[0] += 1
                if call_count[0] == 1:
                    return mock_select_chain
                return mock_update_chain
            return MagicMock()

        return table_side_effect

    @contextmanager
    def _mock_external_http(self):
        """Context manager to mock all external HTTP calls (Roblox APIs)."""
        mock_rget = MagicMock()

        mock_user_response = MagicMock()
        mock_user_response.json.return_value = {"id": 123, "name": "testuser", "displayName": "Test", "created": "2020-01-01T00:00:00Z"}
        mock_user_response.raise_for_status = MagicMock()

        mock_avatar_response = MagicMock()
        mock_avatar_response.json.return_value = {"playerAvatarType": "R15", "scales": {}, "bodyColors": {}, "assets": []}
        mock_avatar_response.raise_for_status = MagicMock()

        mock_thumb_response = MagicMock()
        mock_thumb_response.json.return_value = {"data": [{"imageUrl": "http://example.com/thumb.png"}]}
        mock_thumb_response.raise_for_status = MagicMock()

        def side_effect(url, **kwargs):
            if "users.roblox.com" in url:
                return mock_user_response
            elif "avatar.roblox.com" in url:
                return mock_avatar_response
            elif "thumbnails.roblox.com" in url:
                return mock_thumb_response
            return MagicMock()

        mock_rget.side_effect = side_effect

        # Also mock cache functions
        mock_cache_get = MagicMock(return_value=None)
        mock_cache_set = MagicMock()
        mock_resolve = MagicMock(return_value=123)

        with patch('main.rget', mock_rget), \
             patch('main.cache_get', mock_cache_get), \
             patch('main.cache_set', mock_cache_set), \
             patch('main.resolve', mock_resolve):
            yield


class TestHashFunctions(BaseTest):
    """Test 1: SHA-256 hash-only storage and key shown once."""

    def test_generate_api_key_format(self):
        """Full key format: rbx3d_<43-char-token>, prefix is first 8 chars."""
        full_key, prefix = generate_api_key()
        assert full_key.startswith("rbx3d_")
        # token is 43 chars (32 bytes base64url), so full key is 6 + 43 = 49 chars
        assert len(full_key) == 6 + 43
        assert len(prefix) == 8
        assert full_key[6:14] == prefix  # prefix is first 8 chars of token

    def test_hash_api_key_is_sha256(self):
        """hash_api_key returns SHA-256 hex digest (64 chars)."""
        key = "rbx3d_test_token_123456789012345678901234"
        hashed = hash_api_key(key)
        assert len(hashed) == 64  # SHA-256 hex = 64 chars
        assert all(c in '0123456789abcdef' for c in hashed)
        # Verify it's deterministic
        assert hash_api_key(key) == hashed

    def test_verify_api_key_constant_time(self):
        """verify_api_key uses hmac.compare_digest for constant-time comparison."""
        key = "rbx3d_test_token_123456789012345678901234"
        key_hash = hash_api_key(key)
        assert verify_api_key(key, key_hash) is True
        assert verify_api_key("wrong_key", key_hash) is False
        # Wrong hash same length
        wrong_hash = "0" * 64
        assert verify_api_key(key, wrong_hash) is False

    def test_key_shown_once_not_stored(self):
        """Full key is never stored - only hash is stored."""
        full_key, prefix = generate_api_key()
        key_hash = hash_api_key(full_key)
        # The prefix is stored, not the full key
        assert prefix == full_key[6:14]
        # Hash cannot be reversed to get full key
        assert len(key_hash) == 64
        assert full_key not in key_hash


class TestHashLookup(BaseTest):
    """Test 2: Lookup by hash with identical generic 401 for all failure cases.
    Tests the require_api_key and dual_auth decorator logic directly."""

    def test_require_api_key_expired_key_returns_generic_401(self):
        """Expired key returns generic 'Invalid credentials' 401, not 'expired'."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            mock_supabase.table.return_value = self._mock_supabase_select(return_data=[])

            response = self.client.get('/_test_require_api_key',
                headers={"Authorization": "Bearer rbx3d_expiredkey123456789012345678901234"})
            assert response.status_code == 401
            data = response.get_json()
            assert data['error'] == 'Invalid credentials'
            assert data['error_code'] == 'invalid_credentials'

    def test_require_api_key_revoked_key_returns_generic_401(self):
        """Revoked key returns generic 'Invalid credentials' 401."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            mock_supabase.table.return_value = self._mock_supabase_select(return_data=[])

            response = self.client.get('/_test_require_api_key',
                headers={"Authorization": "Bearer rbx3d_revokedkey123456789012345678901234"})
            assert response.status_code == 401
            data = response.get_json()
            assert data['error'] == 'Invalid credentials'
            assert data['error_code'] == 'invalid_credentials'

    def test_require_api_key_unknown_key_returns_generic_401(self):
        """Unknown key returns generic 'Invalid credentials' 401."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            mock_supabase.table.return_value = self._mock_supabase_select(return_data=[])

            response = self.client.get('/_test_require_api_key',
                headers={"Authorization": "Bearer rbx3d_unknownkey123456789012345678901234"})
            assert response.status_code == 401
            data = response.get_json()
            assert data['error'] == 'Invalid credentials'
            assert data['error_code'] == 'invalid_credentials'

    def test_require_api_key_malformed_key_returns_generic_401(self):
        """Malformed key returns generic 'Invalid credentials' 401."""
        malformed_keys = [
            "rbx3d_",  # too short
            "wrongprefix_token123456789012345678901234",
            "not_a_key_at_all",
            "",
            "rbx3d_short",
        ]
        for bad_key in malformed_keys:
            with patch('main.API_KEY_SUPABASE') as mock_supabase:
                mock_supabase.table.return_value = self._mock_supabase_select(return_data=[])
                response = self.client.get('/_test_require_api_key',
                    headers={"Authorization": f"Bearer {bad_key}"})
                assert response.status_code == 401, f"Failed for key: {bad_key}"
                data = response.get_json()
                assert data['error'] == 'Invalid credentials', f"Failed for key: {bad_key}"
                assert data['error_code'] == 'invalid_credentials', f"Failed for key: {bad_key}"

    def test_require_api_key_valid_key_accepted(self):
        """Valid key is accepted and sets g.api_key_user_id."""
        valid_key = "rbx3d_validtoken123456789012345678901234"
        key_hash = hash_api_key(valid_key)
        mock_row = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "user_id": "12345",
            "key_hash": key_hash,
            "scopes": ["catalog"],
            "rate_limit_rpm": 60,
            "rate_limit_rph": 1000,
        }
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            mock_supabase.table.return_value = self._mock_supabase_select(return_data=[mock_row])
            with patch('main.check_api_key_rate_limit', return_value=None):
                response = self.client.get('/_test_require_api_key',
                    headers={"Authorization": f"Bearer {valid_key}"})
                assert response.status_code == 200
                data = response.get_json()
                assert data['ok'] is True

    def test_dual_auth_same_generic_401(self):
        """dual_auth also returns generic 401 for invalid keys."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            mock_supabase.table.return_value = self._mock_supabase_select(return_data=[])

            response = self.client.get('/_test_dual_auth',
                headers={"Authorization": "Bearer rbx3d_badkey123456789012345678901234"})
            assert response.status_code == 401
            data = response.get_json()
            assert data['error'] == 'Invalid credentials'
            assert data['error_code'] == 'invalid_credentials'


class TestAdminScope(BaseTest):
    """Test 3: admin scope -> 403 (not allowed for users)."""

    def test_admin_scope_rejected_in_create(self):
        """Creating key with 'admin' scope returns 400."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            with self.client.session_transaction() as sess:
                sess['user'] = {'user_id': '12345', 'expiry': 9999999999}

            # Mock active key count check (0 active keys)
            mock_chain = MagicMock()
            mock_response = MagicMock()
            mock_response.count = 0
            mock_chain.execute.return_value = mock_response
            mock_chain.eq.return_value = mock_chain
            mock_chain.is_.return_value = mock_chain
            mock_chain.gt.return_value = mock_chain
            mock_chain.select.return_value = mock_chain
            mock_supabase.table.return_value = mock_chain

            future_date = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat().replace('+00:00', 'Z')
            response = self.client.post('/api/admin/keys', json={
                "name": "Test",
                "scopes": ["admin"],  # admin not allowed
                "expires_at": future_date
            })
            assert response.status_code == 400
            data = response.get_json()
            assert 'scopes must be non-empty subset' in data['error']

    def test_admin_scope_not_in_valid_scopes(self):
        """_VALID_SCOPES does not include 'admin'."""
        assert 'admin' not in _VALID_SCOPES
        assert _VALID_SCOPES == {'catalog', 'avatar'}

    def test_require_api_key_admin_scope_returns_403(self):
        """require_api_key(['admin']) rejects key without admin scope."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            valid_key = "rbx3d_validtoken123456789012345678901234"
            key_hash = hash_api_key(valid_key)
            mock_row = {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "user_id": "12345",
                "key_hash": key_hash,
                "scopes": ["catalog"],  # no admin scope
                "rate_limit_rpm": 60,
                "rate_limit_rph": 1000,
            }
            mock_supabase.table.return_value = self._mock_supabase_select(return_data=[mock_row])
            with patch('main.check_api_key_rate_limit', return_value=None):
                response = self.client.get('/_test_require_api_key_admin',
                    headers={"Authorization": f"Bearer {valid_key}"})
                assert response.status_code == 403
                data = response.get_json()
                assert data['error'] == 'Insufficient scopes'
                assert 'admin' in data['required']


class TestMaxKeys(BaseTest):
    """Test 4: 6th active key -> 409 too_many_keys."""

    def test_6th_key_returns_409(self):
        """Creating 6th active key returns 409 with error_code too_many_keys."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            with self.client.session_transaction() as sess:
                sess['user'] = {'user_id': '12345', 'expiry': 9999999999}

            # Mock active key count check (5 active keys)
            mock_chain = MagicMock()
            mock_response = MagicMock()
            mock_response.count = 5  # At limit
            mock_chain.execute.return_value = mock_response
            mock_chain.eq.return_value = mock_chain
            mock_chain.is_.return_value = mock_chain
            mock_chain.gt.return_value = mock_chain
            mock_chain.select.return_value = mock_chain
            mock_supabase.table.return_value = mock_chain

            future_date = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat().replace('+00:00', 'Z')
            response = self.client.post('/api/admin/keys', json={
                "name": "Test Key 6",
                "scopes": ["catalog"],
                "expires_at": future_date
            })
            assert response.status_code == 409
            data = response.get_json()
            assert data['error_code'] == 'too_many_keys'
            assert 'Maximum 5 active keys' in data['error']

    def test_5th_key_allowed(self):
        """Creating 5th active key is allowed (returns 201)."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            with self.client.session_transaction() as sess:
                sess['user'] = {'user_id': '12345', 'expiry': 9999999999}

            mock_supabase.table.side_effect = self._mock_supabase_for_create(4)

            future_date = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat().replace('+00:00', 'Z')
            response = self.client.post('/api/admin/keys', json={
                "name": "Test Key 5",
                "scopes": ["catalog"],
                "expires_at": future_date
            })
            assert response.status_code == 201
            data = response.get_json()
            assert 'key' in data


class TestExpiryValidation(BaseTest):
    """Test 5: expiry missing/past/over 365 days rejected."""

    def test_missing_expiry_rejected(self):
        """Missing expires_at returns 400."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            with self.client.session_transaction() as sess:
                sess['user'] = {'user_id': '12345', 'expiry': 9999999999}

            mock_supabase.table.side_effect = self._mock_supabase_for_create(0)

            response = self.client.post('/api/admin/keys', json={
                "name": "Test",
                "scopes": ["catalog"]
                # no expires_at
            })
            assert response.status_code == 400
            data = response.get_json()
            assert 'expires_at must be valid ISO 8601' in data['error']

    def test_past_expiry_rejected(self):
        """Past expires_at returns 400."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            with self.client.session_transaction() as sess:
                sess['user'] = {'user_id': '12345', 'expiry': 9999999999}

            mock_supabase.table.side_effect = self._mock_supabase_for_create(0)

            past_date = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace('+00:00', 'Z')
            response = self.client.post('/api/admin/keys', json={
                "name": "Test",
                "scopes": ["catalog"],
                "expires_at": past_date
            })
            assert response.status_code == 400
            data = response.get_json()
            assert 'expires_at must be in the future' in data['error']

    def test_over_365_days_rejected(self):
        """expires_at > 365 days returns 400."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            with self.client.session_transaction() as sess:
                sess['user'] = {'user_id': '12345', 'expiry': 9999999999}

            mock_supabase.table.side_effect = self._mock_supabase_for_create(0)

            far_future = (datetime.now(timezone.utc) + timedelta(days=366)).isoformat().replace('+00:00', 'Z')
            response = self.client.post('/api/admin/keys', json={
                "name": "Test",
                "scopes": ["catalog"],
                "expires_at": far_future
            })
            assert response.status_code == 400
            data = response.get_json()
            assert 'expires_at too far in future' in data['error']

    def test_valid_expiry_accepted(self):
        """Valid expires_at (within 365 days) is accepted."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            with self.client.session_transaction() as sess:
                sess['user'] = {'user_id': '12345', 'expiry': 9999999999}

            mock_supabase.table.side_effect = self._mock_supabase_for_create(0)

            future_date = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat().replace('+00:00', 'Z')
            response = self.client.post('/api/admin/keys', json={
                "name": "Test",
                "scopes": ["catalog"],
                "expires_at": future_date
            })
            assert response.status_code == 201


class TestSupabaseFailure(BaseTest):
    """Test 6: Supabase failure on key-bearing request -> 503 with no exception text."""

    def test_require_api_key_supabase_fail_returns_503(self):
        """@require_api_key returns 503 when Supabase fails."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            mock_supabase.table.return_value = self._make_failing_supabase()

            response = self.client.get('/_test_require_api_key',
                headers={"Authorization": "Bearer rbx3d_validtoken123456789012345678901234"})
            assert response.status_code == 503
            data = response.get_json()
            assert data['error'] == 'Authentication service unavailable'
            assert data['error_code'] == 'api_keys_unavailable'
            assert 'Connection failed' not in str(data)

    def test_dual_auth_supabase_fail_returns_503(self):
        """@dual_auth returns 503 when API key presented and Supabase fails."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            mock_supabase.table.return_value = self._make_failing_supabase()

            response = self.client.get('/_test_dual_auth',
                headers={"Authorization": "Bearer rbx3d_validtoken123456789012345678901234"})
            assert response.status_code == 503
            data = response.get_json()
            assert data['error'] == 'Authentication service unavailable'
            assert data['error_code'] == 'api_keys_unavailable'

    def test_create_key_supabase_fail_returns_503(self):
        """create_api_key returns 503 when Supabase fails."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            with self.client.session_transaction() as sess:
                sess['user'] = {'user_id': '12345', 'expiry': 9999999999}

            mock_supabase.table.return_value = self._make_failing_supabase()

            future_date = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat().replace('+00:00', 'Z')
            response = self.client.post('/api/admin/keys', json={
                "name": "Test",
                "scopes": ["catalog"],
                "expires_at": future_date
            })
            assert response.status_code == 503
            data = response.get_json()
            assert data['error'] == 'Authentication service unavailable'
            assert data['error_code'] == 'api_keys_unavailable'

    def test_list_keys_supabase_fail_returns_503(self):
        """list_api_keys returns 503 when Supabase fails."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            with self.client.session_transaction() as sess:
                sess['user'] = {'user_id': '12345', 'expiry': 9999999999}

            mock_supabase.table.return_value = self._make_failing_supabase()

            response = self.client.get('/api/admin/keys')
            assert response.status_code == 503
            data = response.get_json()
            assert data['error'] == 'Authentication service unavailable'
            assert data['error_code'] == 'api_keys_unavailable'

    def test_revoke_key_supabase_fail_returns_503(self):
        """revoke_api_key returns 503 when Supabase fails."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            with self.client.session_transaction() as sess:
                sess['user'] = {'user_id': '12345', 'expiry': 9999999999}

            mock_supabase.table.return_value = self._make_failing_supabase()

            response = self.client.delete('/api/admin/keys/550e8400-e29b-41d4-a716-446655440000')
            assert response.status_code == 503
            data = response.get_json()
            assert data['error'] == 'Authentication service unavailable'
            assert data['error_code'] == 'api_keys_unavailable'


class TestKeyInQueryString(BaseTest):
    """Test 7: Key in query string ignored (only Authorization header accepted)."""

    def test_require_api_key_query_string_ignored(self):
        """API key passed as query parameter is ignored, returns 401."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            mock_chain = MagicMock()
            mock_response = MagicMock()
            mock_response.data = []
            mock_chain.execute.return_value = mock_response
            mock_chain.eq.return_value = mock_chain
            mock_chain.is_.return_value = mock_chain
            mock_chain.gt.return_value = mock_chain
            mock_supabase.table.return_value = mock_chain

            valid_key = "rbx3d_validtoken123456789012345678901234"
            # Pass key in query string (should be ignored)
            response = self.client.get(f'/_test_require_api_key_qs?api_key={valid_key}')
            # Should get 401 (no valid Authorization header)
            assert response.status_code == 401
            data = response.get_json()
            assert data['error_code'] == 'invalid_credentials'

    def test_require_api_key_header_works(self):
        """API key in Authorization header works."""
        valid_key = "rbx3d_validtoken123456789012345678901234"
        key_hash = hash_api_key(valid_key)
        mock_row = {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "user_id": "12345",
            "key_hash": key_hash,
            "scopes": ["catalog"],
            "rate_limit_rpm": 60,
            "rate_limit_rph": 1000,
        }
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            class MockResponse:
                def __init__(self, data):
                    self.data = data

            mock_chain = MagicMock()
            mock_chain.execute.return_value = MockResponse([mock_row])
            mock_chain.select.return_value = mock_chain
            mock_chain.eq.return_value = mock_chain
            mock_chain.is_.return_value = mock_chain
            mock_chain.gt.return_value = mock_chain
            mock_supabase.table.return_value = mock_chain

            with patch('main.check_api_key_rate_limit', return_value=None):
                response = self.client.get('/_test_require_api_key_qs',
                    headers={"Authorization": f"Bearer {valid_key}"})
                assert response.status_code == 200
                data = response.get_json()
                assert data['ok'] is True

    def test_dual_auth_query_string_ignored(self):
        """dual_auth also ignores query string API key - returns auth_required since no Authorization header."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            class MockResponse:
                def __init__(self, data):
                    self.data = data

            mock_chain = MagicMock()
            mock_chain.execute.return_value = MockResponse([])
            mock_chain.select.return_value = mock_chain
            mock_chain.eq.return_value = mock_chain
            mock_chain.is_.return_value = mock_chain
            mock_chain.gt.return_value = mock_chain
            mock_supabase.table.return_value = mock_chain

            valid_key = "rbx3d_validtoken123456789012345678901234"
            # No Authorization header, key in query string is ignored
            response = self.client.get(f'/_test_dual_auth_qs?api_key={valid_key}')
            assert response.status_code == 401
            data = response.get_json()
            # dual_auth returns auth_required when no Authorization header at all
            assert data['error_code'] == 'auth_required'


class TestKeyIsolation(BaseTest):
    """Test 8: Another user's key can't be listed or revoked."""

    def test_list_keys_only_returns_own_keys(self):
        """list_api_keys only returns keys for the authenticated user."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            with self.client.session_transaction() as sess:
                sess['user'] = {'user_id': '12345', 'expiry': 9999999999}

            # Mock returns only user 12345's keys
            mock_supabase.table.return_value = self._mock_supabase_list_keys([
                {"id": "key1", "key_prefix": "abc1", "name": "Key 1", "scopes": ["catalog"],
                 "expires_at": "2026-12-31T23:59:59Z", "created_at": "2026-01-01T00:00:00Z",
                 "revoked_at": None, "last_used_at": None, "rate_limit_rpm": 60, "rate_limit_rph": 1000},
                {"id": "key2", "key_prefix": "abc2", "name": "Key 2", "scopes": ["catalog"],
                 "expires_at": "2026-12-31T23:59:59Z", "created_at": "2026-01-01T00:00:00Z",
                 "revoked_at": None, "last_used_at": None, "rate_limit_rpm": 60, "rate_limit_rph": 1000},
            ])

            response = self.client.get('/api/admin/keys')
            assert response.status_code == 200
            data = response.get_json()
            assert len(data['keys']) == 2

    def test_revoke_other_users_key_returns_404(self):
        """Revoking another user's key returns 404 (not found)."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            with self.client.session_transaction() as sess:
                sess['user'] = {'user_id': '12345', 'expiry': 9999999999}

            # Mock ownership check returns empty (key belongs to different user)
            mock_supabase.table.side_effect = self._mock_supabase_revoke(key_exists=False)

            response = self.client.delete('/api/admin/keys/550e8400-e29b-41d4-a716-446655440000')
            assert response.status_code == 404
            data = response.get_json()
            assert data['error'] == 'Key not found'

    def test_revoke_own_key_works(self):
        """Revoking own key works."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            with self.client.session_transaction() as sess:
                sess['user'] = {'user_id': '12345', 'expiry': 9999999999}

            mock_supabase.table.side_effect = self._mock_supabase_revoke(key_exists=True, key_user_id="12345")

            response = self.client.delete('/api/admin/keys/550e8400-e29b-41d4-a716-446655440000')
            assert response.status_code == 200
            data = response.get_json()
            assert data['success'] is True


class TestKeyManagementRoutes(BaseTest):
    """Test: Key-management routes accept login session only, not API keys."""

    def test_create_key_with_session_works(self):
        """POST /api/admin/keys works with session auth."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            with self.client.session_transaction() as sess:
                sess['user'] = {'user_id': '12345', 'expiry': 9999999999}

            mock_supabase.table.side_effect = self._mock_supabase_for_create(0)

            future_date = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat().replace('+00:00', 'Z')
            response = self.client.post('/api/admin/keys', json={
                "name": "Test",
                "scopes": ["catalog"],
                "expires_at": future_date
            })
            assert response.status_code == 201
            data = response.get_json()
            assert 'key' in data

    def test_create_key_with_api_key_returns_401(self):
        """POST /api/admin/keys with API key (not session) returns 401 auth_required."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            # Present an API key but no session
            valid_key = "rbx3d_validtoken123456789012345678901234"
            key_hash = hash_api_key(valid_key)
            mock_row = {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "user_id": "12345",
                "key_hash": key_hash,
                "scopes": ["catalog", "admin"],  # Even with admin scope
                "rate_limit_rpm": 60,
                "rate_limit_rph": 1000,
            }
            mock_chain = MagicMock()
            mock_response = MagicMock()
            mock_response.data = [mock_row]
            mock_chain.execute.return_value = mock_response
            mock_chain.eq.return_value = mock_chain
            mock_chain.is_.return_value = mock_chain
            mock_chain.gt.return_value = mock_chain
            mock_supabase.table.return_value = mock_chain

            with patch('main.check_api_key_rate_limit', return_value=None):
                future_date = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat().replace('+00:00', 'Z')
                response = self.client.post('/api/admin/keys', json={
                    "name": "Test",
                    "scopes": ["catalog"],
                    "expires_at": future_date
                }, headers={"Authorization": f"Bearer {valid_key}"})
                # @require_session on key mgmt routes requires SESSION, not API key
                assert response.status_code == 401
                data = response.get_json()
                assert data['error_code'] == 'auth_required'

    def test_list_keys_with_session_works(self):
        """GET /api/admin/keys works with session auth."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            with self.client.session_transaction() as sess:
                sess['user'] = {'user_id': '12345', 'expiry': 9999999999}

            mock_supabase.table.return_value = self._mock_supabase_list_keys([])

            response = self.client.get('/api/admin/keys')
            assert response.status_code == 200

    def test_list_keys_with_api_key_returns_401(self):
        """GET /api/admin/keys with API key returns 401 auth_required."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            valid_key = "rbx3d_validtoken123456789012345678901234"
            key_hash = hash_api_key(valid_key)
            mock_row = {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "user_id": "12345",
                "key_hash": key_hash,
                "scopes": ["catalog", "admin"],
                "rate_limit_rpm": 60,
                "rate_limit_rph": 1000,
            }
            mock_chain = MagicMock()
            mock_response = MagicMock()
            mock_response.data = [mock_row]
            mock_chain.execute.return_value = mock_response
            mock_chain.eq.return_value = mock_chain
            mock_chain.is_.return_value = mock_chain
            mock_chain.gt.return_value = mock_chain
            mock_supabase.table.return_value = mock_chain

            with patch('main.check_api_key_rate_limit', return_value=None):
                response = self.client.get('/api/admin/keys',
                    headers={"Authorization": f"Bearer {valid_key}"})
                assert response.status_code == 401
                data = response.get_json()
                assert data['error_code'] == 'auth_required'

    def test_revoke_key_with_session_works(self):
        """DELETE /api/admin/keys/<id> works with session auth."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            with self.client.session_transaction() as sess:
                sess['user'] = {'user_id': '12345', 'expiry': 9999999999}

            mock_supabase.table.side_effect = self._mock_supabase_revoke(key_exists=True, key_user_id="12345")

            response = self.client.delete('/api/admin/keys/550e8400-e29b-41d4-a716-446655440000')
            assert response.status_code == 200

    def test_revoke_key_with_api_key_returns_401(self):
        """DELETE /api/admin/keys/<id> with API key returns 401 auth_required."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            valid_key = "rbx3d_validtoken123456789012345678901234"
            key_hash = hash_api_key(valid_key)
            mock_row = {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "user_id": "12345",
                "key_hash": key_hash,
                "scopes": ["catalog", "admin"],
                "rate_limit_rpm": 60,
                "rate_limit_rph": 1000,
            }
            mock_chain = MagicMock()
            mock_response = MagicMock()
            mock_response.data = [mock_row]
            mock_chain.execute.return_value = mock_response
            mock_chain.eq.return_value = mock_chain
            mock_chain.is_.return_value = mock_chain
            mock_chain.gt.return_value = mock_chain
            mock_supabase.table.return_value = mock_chain

            with patch('main.check_api_key_rate_limit', return_value=None):
                response = self.client.delete('/api/admin/keys/550e8400-e29b-41d4-a716-446655440000',
                    headers={"Authorization": f"Bearer {valid_key}"})
                assert response.status_code == 401
                data = response.get_json()
                assert data['error_code'] == 'auth_required'

    def test_no_auth_returns_401(self):
        """All key-management routes return 401 with no auth at all."""
        endpoints = [
            ("POST", "/api/admin/keys", {"name": "Test", "scopes": ["catalog"], "expires_at": "2026-12-31T23:59:59Z"}),
            ("GET", "/api/admin/keys", None),
            ("DELETE", "/api/admin/keys/550e8400-e29b-41d4-a716-446655440000", None),
        ]
        for method, endpoint, body in endpoints:
            response = self.client.open(endpoint, method=method, json=body)
            assert response.status_code == 401
            data = response.get_json()
            assert data['error_code'] == 'auth_required'


class TestPublicRoutesNoAuth(BaseTest):
    """Test: Public routes work without any authentication."""

    def test_public_routes_no_auth_required(self):
        """Public routes return 200/400/404 but NOT 401/503."""
        with self._mock_external_http():
            # Also mock Supabase to fail - should not affect public routes
            with patch('main.API_KEY_SUPABASE') as mock_supabase:
                mock_supabase.table.return_value = self._make_failing_supabase()

                public_endpoints = [
                    ("GET", "/api/avatar/info?user=testuser"),
                    ("GET", "/api/avatar/wearing?user=testuser"),
                    ("GET", "/api/catalog/info?id=123"),
                    ("GET", "/api/catalog/browse"),
                    ("GET", "/api/v2/item?id=123"),
                    ("GET", "/api/v2/model/info?id=123"),
                    ("GET", "/health"),
                    ("GET", "/api/audio/ping"),
                ]

                for method, endpoint in public_endpoints:
                    response = self.client.open(endpoint, method=method)
                    # Should NOT be 401 or 503
                    assert response.status_code not in (401, 503), \
                        f"{method} {endpoint} returned {response.status_code} (should not be 401/503)"
                    print(f"  ✓ {method} {endpoint} -> {response.status_code} (not 401/503)")


class TestNoAuthWithSupabaseFail(BaseTest):
    """Test: POST /api/admin/keys with NO session and NO key returns 401 auth_required,
    and 503 only for a logged-in user when Supabase fails."""

    def test_no_session_no_key_returns_401(self):
        """POST /api/admin/keys with no session and no key returns 401 auth_required."""
        future_date = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat().replace('+00:00', 'Z')
        response = self.client.post('/api/admin/keys', json={
            "name": "Test",
            "scopes": ["catalog"],
            "expires_at": future_date
        })
        assert response.status_code == 401
        data = response.get_json()
        assert data['error_code'] == 'auth_required'

    def test_session_with_supabase_fail_returns_503(self):
        """POST /api/admin/keys with session but Supabase fails returns 503."""
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            with self.client.session_transaction() as sess:
                sess['user'] = {'user_id': '12345', 'expiry': 9999999999}

            mock_supabase.table.return_value = self._make_failing_supabase()

            future_date = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat().replace('+00:00', 'Z')
            response = self.client.post('/api/admin/keys', json={
                "name": "Test",
                "scopes": ["catalog"],
                "expires_at": future_date
            })
            assert response.status_code == 503
            data = response.get_json()
            assert data['error_code'] == 'api_keys_unavailable'


def run_tests():
    """Run all tests and return pass/fail count."""
    import subprocess
    result = subprocess.run([
        sys.executable, '-m', 'pytest', __file__, '-v', '--tb=short'
    ], capture_output=True, text=True, cwd=os.path.dirname(__file__))
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode == 0


if __name__ == '__main__':
    print("=" * 60)
    print("API KEY REQUIREMENTS TEST SUITE")
    print("=" * 60)
    success = run_tests()
    sys.exit(0 if success else 1)