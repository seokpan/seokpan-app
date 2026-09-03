"""Versioned Lua sources for atomic Session lifecycle transitions."""

from seokpan.persistence.redis.common import VersionedLuaScript

_COMMON = """
local function now_ms()
  local current = redis.call('TIME')
  return current[1] * 1000 + math.floor(current[2] / 1000)
end

local function member_index(actor_type, actor_id)
  if actor_type ~= 'MEMBER' then
    return nil
  end
  return 'stone:v1:identity:member:' .. actor_id .. ':sessions'
end

local function refresh_member_index(index_key, current_ms)
  if not index_key then return end
  redis.call('ZREMRANGEBYSCORE', index_key, '-inf', current_ms)
  local latest = redis.call('ZREVRANGE', index_key, 0, 0, 'WITHSCORES')
  if #latest == 0 then
    redis.call('DEL', index_key)
  else
    redis.call('PEXPIREAT', index_key, tonumber(latest[2]))
  end
end

local function response(ok, session, error_code)
  return cjson.encode({ok = ok, session = session, error = error_code})
end
"""

CREATE_SESSION = VersionedLuaScript(
    name="session-create",
    version=1,
    source=_COMMON
    + """
local session_key = KEYS[1]
local index_key = KEYS[2]
local digest = ARGV[1]
local actor_type = ARGV[2]
local actor_id = ARGV[3]
local csrf_digest = ARGV[4]
local schema_version = tonumber(ARGV[5])
local idle_ttl_ms = tonumber(ARGV[6])
local absolute_ttl_ms = tonumber(ARGV[7])

if redis.call('EXISTS', session_key) == 1 then
  return response(false, cjson.null, 'SESSION_ALREADY_EXISTS')
end

local current_ms = now_ms()
local absolute_expires_at_ms = current_ms + absolute_ttl_ms
local idle_expires_at_ms = math.min(current_ms + idle_ttl_ms, absolute_expires_at_ms)
local session = {
  schema_version = schema_version,
  actor_type = actor_type,
  actor_id = actor_id,
  csrf_digest = csrf_digest,
  created_at_ms = current_ms,
  last_activity_at_ms = current_ms,
  absolute_expires_at_ms = absolute_expires_at_ms
}
redis.call('SET', session_key, cjson.encode(session), 'PXAT', idle_expires_at_ms)

if actor_type == 'MEMBER' then
  redis.call('ZADD', index_key, idle_expires_at_ms, digest)
  refresh_member_index(index_key, current_ms)
end

return response(true, session, cjson.null)
""",
)

TOUCH_SESSION = VersionedLuaScript(
    name="session-touch",
    version=1,
    source=_COMMON
    + """
local session_key = KEYS[1]
local digest = ARGV[1]
local idle_ttl_ms = tonumber(ARGV[2])
local raw = redis.call('GET', session_key)
if not raw then
  return response(true, cjson.null, cjson.null)
end

local session = cjson.decode(raw)
local current_ms = now_ms()
if current_ms >= session.absolute_expires_at_ms then
  redis.call('DEL', session_key)
  local index_key = member_index(session.actor_type, session.actor_id)
  if index_key then
    redis.call('ZREM', index_key, digest)
    refresh_member_index(index_key, current_ms)
  end
  return response(true, cjson.null, cjson.null)
end

session.last_activity_at_ms = current_ms
local remaining_ms = session.absolute_expires_at_ms - current_ms
local ttl_ms = math.min(idle_ttl_ms, remaining_ms)
redis.call('SET', session_key, cjson.encode(session), 'PX', ttl_ms)
local index_key = member_index(session.actor_type, session.actor_id)
if index_key then
  redis.call('ZADD', index_key, current_ms + ttl_ms, digest)
  refresh_member_index(index_key, current_ms)
end
return response(true, session, cjson.null)
""",
)

ROTATE_SESSION = VersionedLuaScript(
    name="session-rotate",
    version=1,
    source=_COMMON
    + """
local previous_key = KEYS[1]
local replacement_key = KEYS[2]
local replacement_index_key = KEYS[3]
local previous_digest = ARGV[1]
local replacement_digest = ARGV[2]
local actor_type = ARGV[3]
local actor_id = ARGV[4]
local csrf_digest = ARGV[5]
local schema_version = tonumber(ARGV[6])
local idle_ttl_ms = tonumber(ARGV[7])
local absolute_ttl_ms = tonumber(ARGV[8])

if previous_key == replacement_key then
  return response(false, cjson.null, 'SESSION_ROTATION_REQUIRES_NEW_DIGEST')
end
local previous_raw = redis.call('GET', previous_key)
if not previous_raw then
  return response(false, cjson.null, 'SESSION_NOT_FOUND')
end
if redis.call('EXISTS', replacement_key) == 1 then
  return response(false, cjson.null, 'SESSION_ALREADY_EXISTS')
end

local previous = cjson.decode(previous_raw)
local previous_index_key = member_index(previous.actor_type, previous.actor_id)
local current_ms = now_ms()
redis.call('DEL', previous_key)
if previous_index_key then
  redis.call('ZREM', previous_index_key, previous_digest)
  refresh_member_index(previous_index_key, current_ms)
end

local absolute_expires_at_ms = current_ms + absolute_ttl_ms
local idle_expires_at_ms = math.min(current_ms + idle_ttl_ms, absolute_expires_at_ms)
local replacement = {
  schema_version = schema_version,
  actor_type = actor_type,
  actor_id = actor_id,
  csrf_digest = csrf_digest,
  created_at_ms = current_ms,
  last_activity_at_ms = current_ms,
  absolute_expires_at_ms = absolute_expires_at_ms
}
redis.call('SET', replacement_key, cjson.encode(replacement), 'PXAT', idle_expires_at_ms)
if actor_type == 'MEMBER' then
  redis.call('ZADD', replacement_index_key, idle_expires_at_ms, replacement_digest)
  refresh_member_index(replacement_index_key, current_ms)
end
return response(true, replacement, cjson.null)
""",
)

REVOKE_SESSION = VersionedLuaScript(
    name="session-revoke",
    version=1,
    source=_COMMON
    + """
local session_key = KEYS[1]
local digest = ARGV[1]
local raw = redis.call('GET', session_key)
if not raw then
  return cjson.encode({ok = true, revoked = false, error = cjson.null})
end
local session = cjson.decode(raw)
local current_ms = now_ms()
redis.call('DEL', session_key)
local index_key = member_index(session.actor_type, session.actor_id)
if index_key then
  redis.call('ZREM', index_key, digest)
  refresh_member_index(index_key, current_ms)
end
return cjson.encode({ok = true, revoked = true, error = cjson.null})
""",
)
