#!/usr/bin/env python3
"""
Regression test for API key incident.
Asserts that ONLY the three key-management routes require authentication.
All other routes must remain public (no auth decorator).
"""
import os
import sys
from datetime import datetime, timedelta, timezone
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

from main import app
import inspect


def get_routes_with_auth():
    """Get all routes that have authentication decorators by checking their endpoint functions."""
    routes_with_auth = []

    for rule in app.url_map.iter_rules():
        endpoint = rule.endpoint
        if endpoint == 'static':
            continue
        view_func = app.view_functions.get(endpoint)
        if view_func:
            # Check if the view function has auth decorators by inspecting its closure/wrapper
            func = view_func
            has_auth = False
            auth_type = None

            # Check for dual_auth, require_api_key, or require_session by looking at the wrapper chain
            while hasattr(func, '__wrapped__'):
                func_name = getattr(func, '__name__', '')
                if 'dual_auth' in func_name or 'require_api_key' in func_name or 'require_session' in func_name:
                    has_auth = True
                    if 'dual_auth' in func_name:
                        auth_type = 'dual_auth'
                    elif 'require_api_key' in func_name:
                        auth_type = 'require_api_key'
                    else:
                        auth_type = 'require_session'
                    break
                func = func.__wrapped__

            # Also check the function's qualname for the decorator pattern
            if not has_auth:
                # Check the source file for decorator (simplified check)
                try:
                    source = inspect.getsource(view_func)
                    if '@dual_auth' in source or '@require_api_key' in source or '@require_session' in source:
                        has_auth = True
                        if '@require_session' in source:
                            auth_type = 'require_session'
                        elif '@dual_auth' in source:
                            auth_type = 'dual_auth'
                        else:
                            auth_type = 'require_api_key'
                except:
                    pass

            if has_auth:
                routes_with_auth.append((rule.rule, endpoint, auth_type))

    return routes_with_auth


def _make_failing_supabase():
    """Create a mock Supabase that fails on execute()."""
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


from contextlib import contextmanager

@contextmanager
def _mock_external_http():
    """Context manager to mock all external HTTP calls (Roblox APIs)."""
    from unittest.mock import MagicMock, patch

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


def test_public_routes_no_auth():
    """Test that public routes don't require authentication."""
    print("Testing public routes have no auth decorator...")

    # Routes that should NOT have auth (were public on origin/main)
    public_routes = {
        "/api/avatar/info",
        "/api/avatar/wearing",
        "/api/avatar/3d-urls",
        "/api/avatar/download-full",
        "/api/avatar/procedural-download",
        "/api/avatar/smart-download",
        "/api/v2/avatar",
        "/api/catalog/info",
        "/api/catalog/browse",
        "/api/catalog/items-meta",
        "/api/catalog/texture-variants",
        "/api/catalog/image",
        "/api/catalog/cages",
        "/api/catalog/download-full",
        "/api/v2/item",
        "/api/v2/item-batch",
        "/api/v2/model/info",
        "/api/v2/model/mesh",
        "/api/v2/model/texture",
        "/api/v2/model/download",
        "/api/v2/model/convert",
        "/api/v2/model/mesh-union",
        "/api/audio/ping",
        "/health",
    }

    # Routes that SHOULD have auth (key management)
    auth_required_routes = {
        "/api/admin/keys",  # POST create
        "/api/admin/keys",  # GET list
        "/api/admin/keys/<key_id>",  # DELETE revoke
    }

    # Check all routes in app
    routes_with_auth = get_routes_with_auth()

    print(f"\nRoutes found with auth decorators:")
    for rule, endpoint, auth_type in routes_with_auth:
        print(f"  {rule} -> {endpoint} ({auth_type})")

    # Verify no public routes have auth
    public_routes_with_auth = []
    for rule, endpoint, auth_type in routes_with_auth:
        # Normalize rule for comparison (remove <key_id> variable part)
        normalized_rule = rule.replace('<key_id>', '').rstrip('/')
        if normalized_rule in public_routes or rule in public_routes:
            public_routes_with_auth.append((rule, endpoint, auth_type))

    if public_routes_with_auth:
        print("\n❌ FAIL: Public routes have auth decorators:")
        for rule, endpoint, auth_type in public_routes_with_auth:
            print(f"  {rule} -> {endpoint} ({auth_type})")
        return False

    print("\n✓ PASS: No public routes have auth decorators")

    # Verify key management routes have auth
    key_mgmt_endpoints = {'create_api_key', 'list_api_keys', 'revoke_api_key'}
    found_key_mgmt = set()
    for rule, endpoint, auth_type in routes_with_auth:
        if endpoint in key_mgmt_endpoints:
            found_key_mgmt.add(endpoint)

    missing = key_mgmt_endpoints - found_key_mgmt
    if missing:
        print(f"\n❌ FAIL: Key management routes missing auth: {missing}")
        return False

    print(f"\n✓ PASS: Key management routes have auth: {found_key_mgmt}")
    return True


def test_public_routes_with_supabase_failure():
    """Test that public routes work even when Supabase is unavailable.
    They must NOT return 401 or 503."""
    print("\n\nTesting public routes with Supabase failure...")

    app.config['TESTING'] = True
    client = app.test_client()

    # Mock external HTTP (Roblox APIs)
    with _mock_external_http():
        # Mock Supabase to fail
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            mock_supabase.table.return_value = _make_failing_supabase()

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

            all_passed = True
            for method, endpoint in public_endpoints:
                response = client.open(endpoint, method=method)

                # Should NOT be 401 or 503
                if response.status_code in (401, 503):
                    data = response.get_json()
                    print(f"❌ FAIL: {method} {endpoint} returned {response.status_code}: {data}")
                    all_passed = False
                else:
                    print(f"  ✓ {method} {endpoint} -> {response.status_code} (not 401/503)")

            if all_passed:
                print("\n✓ PASS: Public routes work with Supabase failure (no 401/503)")
            return all_passed


def test_key_routes_return_503_with_supabase_failure():
    """Test that key management routes return 503 when Supabase is unavailable."""
    print("\n\nTesting key management routes return 503 with Supabase failure...")

    app.config['TESTING'] = True
    client = app.test_client()

    with patch('main.API_KEY_SUPABASE') as mock_supabase:
        mock_supabase.table.return_value = _make_failing_supabase()

        with client.session_transaction() as sess:
            sess['user'] = {'user_id': '12345', 'expiry': 9999999999}

        all_passed = True

        # Test create key
        future_date = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat().replace('+00:00', 'Z')
        response = client.post('/api/admin/keys', json={
            "name": "Test",
            "scopes": ["catalog"],
            "expires_at": future_date
        })
        if response.status_code != 503:
            print(f"❌ FAIL: create_api_key returned {response.status_code}, expected 503")
            all_passed = False
        else:
            data = response.get_json()
            if data.get('error_code') != 'api_keys_unavailable':
                print(f"❌ FAIL: create_api_key missing error_code: {data}")
                all_passed = False
            else:
                print(f"  ✓ POST /api/admin/keys -> 503 api_keys_unavailable")

        # Test list keys
        response = client.get('/api/admin/keys')
        if response.status_code != 503:
            print(f"❌ FAIL: list_api_keys returned {response.status_code}, expected 503")
            all_passed = False
        else:
            data = response.get_json()
            if data.get('error_code') != 'api_keys_unavailable':
                print(f"❌ FAIL: list_api_keys missing error_code: {data}")
                all_passed = False
            else:
                print(f"  ✓ GET /api/admin/keys -> 503 api_keys_unavailable")

        # Test revoke key
        test_key_id = "550e8400-e29b-41d4-a716-446655440000"
        response = client.delete(f'/api/admin/keys/{test_key_id}')
        if response.status_code != 503:
            print(f"❌ FAIL: revoke_api_key returned {response.status_code}, expected 503")
            all_passed = False
        else:
            data = response.get_json()
            if data.get('error_code') != 'api_keys_unavailable':
                print(f"❌ FAIL: revoke_api_key missing error_code: {data}")
                all_passed = False
            else:
                print(f"  ✓ DELETE /api/admin/keys/{test_key_id} -> 503 api_keys_unavailable")

    if all_passed:
        print("\n✓ PASS: Key management routes return 503 with Supabase failure")
    return all_passed


def test_key_mgmt_no_session_no_key_returns_401():
    """Test POST /api/admin/keys with NO session and NO key returns 401 auth_required."""
    print("\n\nTesting key mgmt: no session + no key -> 401 auth_required...")

    app.config['TESTING'] = True
    client = app.test_client()

    future_date = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat().replace('+00:00', 'Z')
    response = client.post('/api/admin/keys', json={
        "name": "Test",
        "scopes": ["catalog"],
        "expires_at": future_date
    })

    if response.status_code != 401:
        print(f"❌ FAIL: expected 401, got {response.status_code}")
        return False

    data = response.get_json()
    if data.get('error_code') != 'auth_required':
        print(f"❌ FAIL: expected error_code 'auth_required', got {data}")
        return False

    print(f"  ✓ POST /api/admin/keys (no auth) -> 401 auth_required")
    return True


def test_key_mgmt_session_supabase_fail_returns_503():
    """Test POST /api/admin/keys with session but Supabase fails returns 503."""
    print("\n\nTesting key mgmt: session + Supabase fail -> 503...")

    app.config['TESTING'] = True
    client = app.test_client()

    with patch('main.API_KEY_SUPABASE') as mock_supabase:
        mock_supabase.table.return_value = _make_failing_supabase()

        with client.session_transaction() as sess:
            sess['user'] = {'user_id': '12345', 'expiry': 9999999999}

        future_date = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat().replace('+00:00', 'Z')
        response = client.post('/api/admin/keys', json={
            "name": "Test",
            "scopes": ["catalog"],
            "expires_at": future_date
        })

        if response.status_code != 503:
            print(f"❌ FAIL: expected 503, got {response.status_code}")
            return False

        data = response.get_json()
        if data.get('error_code') != 'api_keys_unavailable':
            print(f"❌ FAIL: expected error_code 'api_keys_unavailable', got {data}")
            return False

        print(f"  ✓ POST /api/admin/keys (session + Supabase fail) -> 503 api_keys_unavailable")
        return True


def test_key_mgmt_routes_reject_api_keys():
    """Test that key-management routes only accept session auth, not API keys."""
    print("\n\nTesting key mgmt routes reject API keys (require session)...")

    app.config['TESTING'] = True
    client = app.test_client()

    from main import hash_api_key
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

    all_passed = True

    for endpoint, method, body in [
        ("/api/admin/keys", "POST", {"name": "Test", "scopes": ["catalog"], "expires_at": "2026-12-31T23:59:59Z"}),
        ("/api/admin/keys", "GET", None),
        ("/api/admin/keys/550e8400-e29b-41d4-a716-446655440000", "DELETE", None),
    ]:
        with patch('main.API_KEY_SUPABASE') as mock_supabase:
            mock_chain = MagicMock()
            mock_response = MagicMock()
            mock_response.data = [mock_row]
            mock_chain.execute.return_value = mock_response
            mock_chain.eq.return_value = mock_chain
            mock_chain.is_.return_value = mock_chain
            mock_chain.gt.return_value = mock_chain
            mock_supabase.table.return_value = mock_chain

            with patch('main.check_api_key_rate_limit', return_value=None):
                response = client.open(endpoint, method=method, json=body,
                    headers={"Authorization": f"Bearer {valid_key}"})

                if response.status_code != 401:
                    print(f"❌ FAIL: {method} {endpoint} with API key returned {response.status_code}, expected 401")
                    all_passed = False
                else:
                    data = response.get_json()
                    if data.get('error_code') != 'auth_required':
                        print(f"❌ FAIL: {method} {endpoint} missing error_code 'auth_required': {data}")
                        all_passed = False
                    else:
                        print(f"  ✓ {method} {endpoint} with API key -> 401 auth_required")

    if all_passed:
        print("\n✓ PASS: Key management routes reject API keys (require session)")
    return all_passed


def main():
    print("=" * 60)
    print("REGRESSION TEST: API Key Incident Fix")
    print("=" * 60)

    all_passed = True

    # Test 1: No public routes have auth
    if not test_public_routes_no_auth():
        all_passed = False

    # Test 2: Public routes work with Supabase failure (no 401/503)
    if not test_public_routes_with_supabase_failure():
        all_passed = False

    # Test 3: Key management routes return 503 with Supabase failure
    if not test_key_routes_return_503_with_supabase_failure():
        all_passed = False

    # Test 4: No session + no key -> 401 auth_required
    if not test_key_mgmt_no_session_no_key_returns_401():
        all_passed = False

    # Test 5: Session + Supabase fail -> 503
    if not test_key_mgmt_session_supabase_fail_returns_503():
        all_passed = False

    # Test 6: Key mgmt routes reject API keys
    if not test_key_mgmt_routes_reject_api_keys():
        all_passed = False

    print("\n" + "=" * 60)
    if all_passed:
        print("ALL TESTS PASSED ✓")
        return 0
    else:
        print("SOME TESTS FAILED ❌")
        return 1


if __name__ == '__main__':
    sys.exit(main())