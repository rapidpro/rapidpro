# -*- coding: utf-8 -*-
import time
from django_redis import get_redis_connection


def push_courier_msgs(channel, msgs, is_bulk=False):
    """
    Adds the passed in msgs to our courier queue for channel
    """
    r = get_redis_connection('default')
    priority = COURIER_BULK_PRIORITY if is_bulk else COURIER_DEFAULT_PRIORITY

    # create our payload
    payload = []
    for msg in msgs:
        payload.append(msg_as_task(msg))

    # call our lua script
    r.eval(LUA_PUSH, 6, int(round(time.time() * 1000)), 'msgs', channel.uuid, channel.tps, priority, payload)


def msg_as_task(msg):
    """
    Used to serialize msgs as tasks to courier
    """
    msg_json = dict(id=msg.id,
                    uuid=unicode(msg.uuid) if msg.uuid else "",
                    org_id=msg.org_id,
                    channel_id=msg.channel_id,
                    channel_uuid=msg.channel.uuid,
                    contact_id=msg.contact_id,
                    contact_urn_id=msg.contact_urn_id,

                    status=msg.status,
                    direction=msg.direction,
                    text=msg.text,
                    priority=msg.priority,
                    urn=msg.contact_urn.urn,
                    error_count=msg.error_count,
                    attachments=msg.attachments,
                    response_to_id=msg.response_to_id,
                    external_id=msg.external_id,

                    next_attempt=msg.next_attempt,
                    created_on=msg.created_on,
                    modified_on=msg.modified_on,
                    queued_on=msg.queued_on,
                    sent_on=msg.sent_on)

    if msg.contact_urn.auth:
        msg_json['contact_urn_auth'] = msg.contact_urn.auth

    return msg_json


COURIER_DEFAULT_PRIORITY = 1
COURIER_BULK_PRIORITY = 0


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
