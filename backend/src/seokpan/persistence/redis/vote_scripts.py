"""Versioned Lua scripts for Vote, Turn, and resolver runtime state."""

from seokpan.persistence.redis.common import VersionedLuaScript

_COMMON = r"""
local operation = ARGV[1]
local request_id = ARGV[2]
local request_ttl_ms = tonumber(ARGV[3])
local resolver_lease_ms = tonumber(ARGV[4])
local payload = cjson.decode(ARGV[5])

local function response(value)
  value.ok = true
  value.error = cjson.null
  return value
end

local function rejection(code)
  return cjson.encode({ok = false, error = code})
end

local function time_ms()
  local value = redis.call('TIME')
  return tonumber(value[1]) * 1000 + math.floor(tonumber(value[2]) / 1000)
end

local function decode_or_nil(value)
  if not value then return nil end
  return cjson.decode(value)
end

local function game_state()
  return decode_or_nil(redis.call('GET', KEYS[4]))
end

local function store_game(game)
  redis.call('SET', KEYS[4], cjson.encode(game))
end

local function current_version(game)
  return tonumber(game.state_version)
end

local function advance_version(game)
  game.state_version = current_version(game) + 1
  store_game(game)
  return game.state_version
end

local function expected_version_matches(game)
  return current_version(game) == payload.expected_state_version
end

local function sorted_hash(key)
  local raw = redis.call('HGETALL', key)
  local values = {}
  for index = 1, #raw, 2 do
    table.insert(values, {key = raw[index], value = raw[index + 1]})
  end
  table.sort(values, function(left, right) return left.key < right.key end)
  return values
end

local function tally_values()
  local values = {}
  for _, item in ipairs(sorted_hash(KEYS[7])) do
    table.insert(values, {coordinate = item.key, count = tonumber(item.value)})
  end
  table.sort(values, function(left, right)
    if left.count == right.count then return left.coordinate < right.coordinate end
    return left.count > right.count
  end)
  return values
end

local function snapshot(game)
  if not game then return cjson.null end
  local participants = {}
  for _, participant in ipairs(game.participants) do
    local current = decode_or_nil(redis.call('HGET', KEYS[2], participant.participant_id))
    local connected = current and current.connected or false
    table.insert(participants, {
      participant_id = participant.participant_id,
      team = participant.team,
      role = participant.role,
      connected = connected
    })
  end
  local votes = {}
  for _, item in ipairs(sorted_hash(KEYS[6])) do
    table.insert(votes, {participant_id = item.key, coordinate = item.value})
  end
  local board = {}
  for _, item in ipairs(sorted_hash(KEYS[5])) do
    table.insert(board, {coordinate = item.key, stone = item.value})
  end
  return {
    schema_version = game.schema_version,
    room_id = payload.room_id,
    game_id = game.game_id,
    state_version = current_version(game),
    turn_no = game.turn_no,
    turn_status = game.turn_status,
    current_team = game.current_team,
    deadline_ms = game.deadline_ms,
    consecutive_passes = game.consecutive_passes,
    move_no = game.move_no,
    game_status = game.game_status,
    end_reason = game.end_reason,
    valid_voter_count = game.valid_voter_count or cjson.null,
    participants = participants,
    votes = votes,
    tally = tally_values(),
    candidates = game.candidates or {},
    occupied_cells = board,
    resolver = decode_or_nil(redis.call('GET', KEYS[8])) or cjson.null
  }
end

local function remember(result)
  local stored = cjson.encode(result)
  redis.call('HSET', KEYS[12], request_id, cjson.encode({
    fingerprint = payload.fingerprint,
    result = result
  }))
  redis.call('ZADD', KEYS[13], time_ms() + request_ttl_ms, request_id)
  return stored
end

local expired = redis.call('ZRANGEBYSCORE', KEYS[13], '-inf', time_ms())
if #expired > 0 then
  redis.call('HDEL', KEYS[12], unpack(expired))
  redis.call('ZREM', KEYS[13], unpack(expired))
end
local cached = decode_or_nil(redis.call('HGET', KEYS[12], request_id))
if cached then
  if cached.fingerprint ~= payload.fingerprint then return rejection('REQUEST_ID_CONFLICT') end
  cached.result.replayed = true
  return cjson.encode(cached.result)
end
"""

_MUTATION = r"""
if operation == 'initialize' then
  if redis.call('EXISTS', KEYS[4]) == 1 then
    return rejection('GAME_RUNTIME_ALREADY_EXISTS')
  end
  if redis.call('EXISTS', KEYS[1]) == 0 then return rejection('ROOM_NOT_FOUND') end
  if redis.call('HGET', KEYS[1], 'status') ~= 'PLAYING'
      or redis.call('HGET', KEYS[1], 'game_id') ~= payload.game_id then
    return rejection('GAME_NOT_IN_CURRENT_ROOM')
  end
  if payload.expected_state_version ~= 1 then return rejection('STATE_VERSION_CONFLICT') end
  local game = {
    schema_version = payload.schema_version,
    game_id = payload.game_id,
    state_version = payload.expected_state_version + 1,
    turn_no = 1,
    turn_status = 'VOTING',
    current_team = 'BLACK',
    deadline_ms = payload.deadline_ms,
    consecutive_passes = 0,
    move_no = 0,
    game_status = 'ACTIVE',
    end_reason = cjson.null,
    valid_voter_count = cjson.null,
    participants = payload.participants,
    candidates = {}
  }
  redis.call('DEL', KEYS[5], KEYS[6], KEYS[7], KEYS[8], KEYS[9], KEYS[10], KEYS[11])
  store_game(game)
  return remember(response({snapshot = snapshot(game)}))
end

local game = game_state()
if not game then return rejection('GAME_RUNTIME_NOT_FOUND') end
if game.game_id ~= payload.game_id then return rejection('STALE_GAME') end
if game.turn_no ~= payload.turn_no then return rejection('STALE_TURN') end
if not expected_version_matches(game) then return rejection('STATE_VERSION_CONFLICT') end

local function participant(participant_id)
  for _, item in ipairs(game.participants) do
    if item.participant_id == participant_id then return item end
  end
  return nil
end

local function eligible(participant_id)
  local item = participant(participant_id)
  if not item then return nil, 'PARTICIPANT_NOT_FOUND' end
  if item.role ~= 'PLAYER' then return nil, 'PLAYER_REQUIRED' end
  local room_value = decode_or_nil(redis.call('HGET', KEYS[2], participant_id))
  if not room_value or not room_value.connected then return nil, 'PARTICIPANT_DISCONNECTED' end
  if item.team ~= game.current_team then return nil, 'CURRENT_TEAM_REQUIRED' end
  return item, nil
end

if operation == 'cast_vote' or operation == 'remove_vote' then
  if game.game_status ~= 'ACTIVE' then return rejection('GAME_NOT_ACTIVE') end
  if game.turn_status ~= 'VOTING' then return rejection('TURN_NOT_VOTING') end
  if time_ms() >= game.deadline_ms then return rejection('TURN_DEADLINE_REACHED') end
  local _, error = eligible(payload.participant_id)
  if error then return rejection(error) end
  local previous = redis.call('HGET', KEYS[6], payload.participant_id)
  if operation == 'cast_vote' then
    if redis.call('HEXISTS', KEYS[5], payload.coordinate) == 1 then
      return rejection('POSITION_OCCUPIED')
    end
    if previous ~= payload.coordinate then
      if previous then
        local remaining = redis.call('HINCRBY', KEYS[7], previous, -1)
        if remaining <= 0 then redis.call('HDEL', KEYS[7], previous) end
      end
      redis.call('HSET', KEYS[6], payload.participant_id, payload.coordinate)
      redis.call('HINCRBY', KEYS[7], payload.coordinate, 1)
      advance_version(game)
    end
  elseif previous then
    redis.call('HDEL', KEYS[6], payload.participant_id)
    local remaining = redis.call('HINCRBY', KEYS[7], previous, -1)
    if remaining <= 0 then redis.call('HDEL', KEYS[7], previous) end
    advance_version(game)
  end
  return remember(response({snapshot = snapshot(game)}))
end

if operation == 'close_turn' then
  if game.game_status ~= 'ACTIVE' then return rejection('GAME_NOT_ACTIVE') end
  if game.turn_status ~= 'VOTING' then return rejection('TURN_NOT_VOTING') end
  if time_ms() < game.deadline_ms then return rejection('TURN_DEADLINE_NOT_REACHED') end
  local tally = tally_values()
  local valid_voter_count = 0
  for _, item in ipairs(game.participants) do
    if item.role == 'PLAYER' and item.team == game.current_team then
      local room_value = decode_or_nil(redis.call('HGET', KEYS[2], item.participant_id))
      if room_value and room_value.connected then valid_voter_count = valid_voter_count + 1 end
    end
  end
  game.valid_voter_count = valid_voter_count
  local closure = {
    game_id = game.game_id,
    turn_no = game.turn_no,
    team = game.current_team,
    tally = tally,
    candidates = {}
  }
  if #tally == 0 then
    local next_passes = game.consecutive_passes + 1
    closure.result = next_passes == 2 and 'JOINT_LOSS' or 'PASSED'
    if next_passes == 2 then
      game.turn_status = 'RESOLVING'
      closure.status = 'RESOLVING'
    else
      game.consecutive_passes = next_passes
      game.turn_status = 'PASSED'
      closure.status = 'PASSED'
      if payload.next_deadline_ms == nil or payload.next_deadline_ms <= game.deadline_ms then
        return rejection('INVALID_NEXT_DEADLINE')
      end
      game.turn_no = game.turn_no + 1
      game.current_team = game.current_team == 'BLACK' and 'WHITE' or 'BLACK'
      game.deadline_ms = payload.next_deadline_ms
      game.turn_status = 'VOTING'
    end
    redis.call('DEL', KEYS[6], KEYS[7], KEYS[8])
  else
    local highest = tally[1].count
    for _, item in ipairs(tally) do
      if item.count == highest then table.insert(closure.candidates, item.coordinate) end
    end
    game.turn_status = 'RESOLVING'
    game.candidates = closure.candidates
    closure.status = 'RESOLVING'
    closure.result = 'RESOLUTION_REQUIRED'
  end
  advance_version(game)
  return remember(response({
    snapshot = snapshot(game), closure = closure, valid_voter_count = valid_voter_count
  }))
end

if operation == 'acquire_resolver' then
  if game.turn_status ~= 'RESOLVING' then return rejection('TURN_NOT_RESOLVING') end
  local current = decode_or_nil(redis.call('GET', KEYS[8]))
  local now = time_ms()
  if current and current.expires_at_ms > now and current.resolution_id ~= payload.resolution_id then
    return rejection('RESOLVER_LEASE_HELD')
  end
  local resolver = {
    resolution_id = payload.resolution_id,
    expires_at_ms = now + resolver_lease_ms
  }
  redis.call('SET', KEYS[8], cjson.encode(resolver))
  return remember(response({snapshot = snapshot(game)}))
end

if operation == 'apply_resolution' then
  if game.turn_status ~= 'RESOLVING' then return rejection('TURN_NOT_RESOLVING') end
  if not payload.persistence_confirmed then
    return rejection('PERSISTENCE_CONFIRMATION_REQUIRED')
  end
  local resolver = decode_or_nil(redis.call('GET', KEYS[8]))
  if not resolver or resolver.resolution_id ~= payload.resolution_id then
    return rejection('RESOLVER_NOT_OWNER')
  end
  if resolver.expires_at_ms <= time_ms() then return rejection('RESOLVER_LEASE_EXPIRED') end
  local resolution = payload.resolution
  if resolution.game_id ~= game.game_id or resolution.turn_no ~= game.turn_no then
    return rejection('RESOLUTION_MISMATCH')
  end
  local move_resolution = resolution.result == 'MOVE_APPLIED'
      and resolution.status == 'MOVE_APPLIED'
      and resolution.team == game.current_team
      and resolution.applied_move
      and resolution.applied_move.team == resolution.team
      and resolution.applied_move.move_no == game.move_no + 1
      and resolution.applied_move.coordinate == resolution.selected_coordinate
  local joint_loss_resolution = resolution.result == 'JOINT_LOSS'
      and resolution.status == 'PASSED'
      and (resolution.selected_coordinate == nil or resolution.selected_coordinate == cjson.null)
      and (resolution.applied_move == nil or resolution.applied_move == cjson.null)
      and resolution.end_reason == 'JOINT_LOSS'
  if not move_resolution and not joint_loss_resolution then
    return rejection('RESOLUTION_MISMATCH')
  end
  if move_resolution then
    local selected = resolution.selected_coordinate
    local valid = false
    for _, candidate in ipairs(game.candidates) do
      if candidate == selected then valid = true end
    end
    if not valid then return rejection('INVALID_RESOLUTION_CANDIDATE') end
    if redis.call('HEXISTS', KEYS[5], selected) == 1 then return rejection('POSITION_OCCUPIED') end
    redis.call('HSET', KEYS[5], selected, resolution.team)
    game.move_no = resolution.applied_move.move_no
    game.consecutive_passes = 0
  else
    game.consecutive_passes = 2
  end
  game.game_status = payload.next_game_status
  game.end_reason = payload.next_end_reason
  game.candidates = {}
  if game.game_status == 'ACTIVE' then
    if payload.next_deadline_ms == nil or payload.next_deadline_ms <= game.deadline_ms then
      return rejection('INVALID_NEXT_DEADLINE')
    end
    game.turn_no = game.turn_no + 1
    game.current_team = game.current_team == 'BLACK' and 'WHITE' or 'BLACK'
    game.deadline_ms = payload.next_deadline_ms
    game.turn_status = 'VOTING'
  else
    game.turn_status = resolution.status
    game.deadline_ms = cjson.null
  end
  redis.call('DEL', KEYS[6], KEYS[7], KEYS[8])
  advance_version(game)
  return remember(response({snapshot = snapshot(game), resolution = resolution}))
end

return rejection('VOTE_OPERATION_INVALID')
"""

VOTE_MUTATION = VersionedLuaScript(
    name="vote-runtime-mutation",
    version=3,
    source=_COMMON + _MUTATION,
)

VOTE_READ = VersionedLuaScript(
    name="vote-runtime-read",
    version=3,
    source=r"""
local payload = cjson.decode(ARGV[1])
local function sorted_hash(key)
  local raw = redis.call('HGETALL', key)
  local values = {}
  for index = 1, #raw, 2 do
    table.insert(values, {key = raw[index], value = raw[index + 1]})
  end
  table.sort(values, function(left, right) return left.key < right.key end)
  return values
end
local raw_game = redis.call('GET', KEYS[3])
if not raw_game then return cjson.encode({ok = true, error = cjson.null, snapshot = cjson.null}) end
local game = cjson.decode(raw_game)
local participants = {}
for _, participant in ipairs(game.participants) do
  local raw = redis.call('HGET', KEYS[2], participant.participant_id)
  local current = raw and cjson.decode(raw) or nil
  local connected = current and current.connected or false
  table.insert(participants, {
    participant_id = participant.participant_id,
    team = participant.team,
    role = participant.role,
    connected = connected
  })
end
local votes = {}
for _, item in ipairs(sorted_hash(KEYS[5])) do
  table.insert(votes, {participant_id = item.key, coordinate = item.value})
end
local tally = {}
for _, item in ipairs(sorted_hash(KEYS[6])) do
  table.insert(tally, {coordinate = item.key, count = tonumber(item.value)})
end
local board = {}
for _, item in ipairs(sorted_hash(KEYS[4])) do
  table.insert(board, {coordinate = item.key, stone = item.value})
end
local raw_resolver = redis.call('GET', KEYS[7])
return cjson.encode({ok = true, error = cjson.null, snapshot = {
  schema_version = game.schema_version,
  room_id = payload.room_id,
  game_id = game.game_id,
  state_version = tonumber(game.state_version),
  turn_no = game.turn_no,
  turn_status = game.turn_status,
  current_team = game.current_team,
  deadline_ms = game.deadline_ms,
  consecutive_passes = game.consecutive_passes,
  move_no = game.move_no,
  game_status = game.game_status,
  end_reason = game.end_reason,
  valid_voter_count = game.valid_voter_count or cjson.null,
  participants = participants,
  votes = votes,
  tally = tally,
  candidates = game.candidates or {},
  occupied_cells = board,
  resolver = raw_resolver and cjson.decode(raw_resolver) or cjson.null
}})
""",
)
