import time

from django_redis import get_redis_connection

from temba.utils import analytics, json


def push_courier_msgs(channel, msgs, high_priority=False):
    """
    Adds the passed in msgs to our courier queue for channel
    """
    r = get_redis_connection("default")
    priority = COURIER_HIGH_PRIORITY if high_priority else COURIER_LOW_PRIORITY
    tps = channel.tps if channel.tps else COURIER_DEFAULT_TPS

    # create our payload
    payload = []
    for msg in msgs:
        payload.append(msg_as_task(msg))

    # call our lua script
    get_script(r)(keys=(time.time(), "msgs", channel.uuid, tps, priority, json.dumps(payload)), client=r)


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
    msg_json = dict(
        id=msg.id,
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
        next_attempt=msg.next_attempt.isoformat() if msg.next_attempt else None,
        created_on=msg.created_on.isoformat(),
        modified_on=msg.modified_on.isoformat(),
        queued_on=msg.queued_on.isoformat() if msg.queued_on else None,
        sent_on=msg.sent_on.isoformat() if msg.sent_on else None,
    )

    if msg.contact_urn.auth:  # pragma: no cover
        msg_json["urn_auth"] = msg.contact_urn.auth

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


def handle_new_message(org, msg):
    """
    Messages created by courier are only saved to the database. Here we take care of the other stuff
    """
    if not msg.topup_id:
        (msg.topup_id, amount) = org.decrement_credit()
        msg.save(update_fields=("topup_id",))

    # set the preferred channel for this contact
    msg.contact.set_preferred_channel(msg.channel)

    # if this contact is stopped, unstop them
    if msg.contact.is_stopped:
        msg.contact.unstop(msg.channel.created_by)

    analytics.gauge("temba.msg_incoming_%s" % msg.channel.channel_type.lower())


def handle_new_contact(org, contact):
    """
    Contacts created by courier are only saved to the database. Here we take care of the other stuff
    """
    # possible to have dynamic groups based on name
    contact.handle_update(fields=("name",), is_new=True, urns=[str(u) for u in contact.get_urns()])

    analytics.gauge("temba.contact_created")
