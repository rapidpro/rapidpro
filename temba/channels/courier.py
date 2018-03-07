# -*- coding: utf-8 -*-
import time
import json
from django_redis import get_redis_connection
from temba.utils.dates import datetime_to_str


def push_courier_msgs(channel, msgs, high_priority=False):
    """
    Adds the passed in msgs to our courier queue for channel
    """
    r = get_redis_connection('default')
    priority = COURIER_HIGH_PRIORITY if high_priority else COURIER_LOW_PRIORITY
    tps = channel.tps if channel.tps else COURIER_DEFAULT_TPS

    # create our payload
    payload = []
    for msg in msgs:
        payload.append(msg_as_task(msg))

    # call our lua script
    get_script(r)(keys=(time.time(), 'msgs', channel.uuid, tps, priority, json.dumps(payload)), client=r)


_script = None


def get_script(r):
    global _script
    if _script:  # pragma: no cover
        return _script

    _script = r.register_script(LUA_PUSH)
    return _script


def msg_as_task(msg):
    """
    Used to serialize msgs as tasks to courier
    """
    msg_json = dict(id=msg.id,
                    uuid=str(msg.uuid) if msg.uuid else "",
                    org_id=msg.org_id,
                    channel_id=msg.channel_id,
                    channel_uuid=msg.channel.uuid,
                    contact_id=msg.contact_id,
                    contact_urn_id=msg.contact_urn_id,

                    status=msg.status,
                    direction=msg.direction,
                    text=msg.text,
                    high_priority=msg.high_priority,
                    urn=msg.contact_urn.urn,
                    error_count=msg.error_count,
                    attachments=msg.attachments,
                    metadata=msg.metadata,
                    response_to_id=msg.response_to_id,
                    response_to_external_id=msg.response_to.external_id if msg.response_to else "",
                    external_id=msg.external_id,

                    tps_cost=msg.channel.calculate_tps_cost(msg),

                    next_attempt=datetime_to_str(msg.next_attempt, ms=True),
                    created_on=datetime_to_str(msg.created_on, ms=True),
                    modified_on=datetime_to_str(msg.modified_on, ms=True),
                    queued_on=datetime_to_str(msg.queued_on, ms=True),
                    sent_on=datetime_to_str(msg.sent_on, ms=True))

    if msg.contact_urn.auth:  # pragma: no cover
        msg_json['urn_auth'] = msg.contact_urn.auth

    return msg_json


COURIER_HIGH_PRIORITY = 1
COURIER_LOW_PRIORITY = 0
COURIER_DEFAULT_TPS = 10


# Our lua script for properly inserting items to a courier queue
# from https://github.com/nyaruka/courier/blob/master/queue/queue.go
LUA_PUSH = """
  -- KEYS: [EpochMS, QueueType, QueueName, TPS, Priority, Value]

  -- first push onto our specific queue
  -- our queue name is built from the type, name and tps, usually something like: "msgs:uuid1-uuid2-uuid3-uuid4|tps"
  local queueKey = KEYS[2] .. ":" .. KEYS[3] .. "|" .. KEYS[4]

  -- our priority queue name also includes the priority of the message (we have one queue for default and one for bulk)
  local priorityQueueKey = queueKey .. "/" .. KEYS[5]
  redis.call("zadd", priorityQueueKey, KEYS[1], KEYS[6])
  local tps = tonumber(KEYS[4])

  -- if we have a TPS, check whether we are currently throttled
  local curr = -1
  if tps > 0 then
    local tpsKey = queueKey .. ":tps:" .. math.floor(KEYS[1])
    curr = tonumber(redis.call("get", tpsKey))
  end

  -- if we aren't then add to our active
  if not curr or curr < tps then
  redis.call("zincrby", KEYS[2] .. ":active", 0, queueKey)
    return 1
  else
    return 0
  end
"""
