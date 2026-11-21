# API Keys RLS Test (run after creating the table)

## Prerequisites
1. Run the migration: `supabase db push` (or apply `supabase/migrations/20261120_create_api_keys.sql` manually in Supabase SQL Editor)
2. Get your **Publishable key** from Supabase Dashboard → Settings → API → Publishable key → `default` (format: `sb_publishable_...`)

## Test: Publishable key MUST be refused (no rows returned, or 401/403)

```bash
# Replace with your actual values
SUPABASE_URL="https://your-project.supabase.co"
PUBLISHABLE_KEY="sb_publishable_your_key_here"

# Attempt to read api_keys with publishable key
curl -s -w "\nHTTP %{http_code}\n" \
  -H "apikey: $PUBLISHABLE_KEY" \
  -H "Authorization: Bearer $PUBLISHABLE_KEY" \
  "$SUPABASE_URL/rest/v1/api_keys?select=*"
```

### Expected Result (RLS working correctly)
```
[]
HTTP 200
```
**OR**
```
{"code":"42501","details":null,"hint":null,"message":"permission denied for table api_keys"}
HTTP 401
```

### What this means
- `[]` with 200 = RLS is on, no policies = zero rows visible to anon/publishable key ✓
- 401/403 permission denied = RLS blocking access ✓
- Any rows returned = **RLS NOT WORKING** ✗

---

## Test: Service role key (your existing SUPABASE_KEY in Vercel) MUST work

```bash
# Use the SAME key already in Vercel as SUPABASE_KEY (RLS-bypassing)
SERVICE_KEY="your_vercel_supabase_key_value"

curl -s -w "\nHTTP %{http_code}\n" \
  -H "apikey: $SERVICE_KEY" \
  -H "Authorization: Bearer $SERVICE_KEY" \
  "$SUPABASE_URL/rest/v1/api_keys?select=*"
```

### Expected Result
```
[]  (or rows if you've created keys via the API)
HTTP 200
```

---

## Header Behavior Verification
The backend uses identical values for both headers (see `_cc_headers` and `API_KEY_SUPABASE` init):

```python
# Both headers get the SAME key value
headers = {
    "apikey": SUPABASE_SERVICE_KEY or SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY or SUPABASE_KEY}"
}
```

**No Supabase call sends different values** in `apikey` vs `Authorization`.

---

## Parameter Validation Summary
All params reaching Supabase are validated:
| Parameter | Validation |
|-----------|------------|
| `key_prefix` | `^[0-9a-f]{4}$` (exactly 4 hex chars) |
| `key_id` | UUID format |
| `user_id` | `^\d+$` (digits only, Roblox user ID) |
| `scopes` | Allowlist: `catalog`, `avatar`, `admin` only |