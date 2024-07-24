import time
from datetime import datetime, timezone as tzone

import google.auth.transport.requests
import requests
from google.oauth2 import service_account

from django.conf import settings

from temba.msgs.models import Msg

from ..models import Channel


def get_sync_commands(msgs):
    """
    Returns the minimal # of broadcast commands for the given Android channel to uniquely represent all the
    messages which are being sent to tel URNs. This will return an array of dicts that look like:
            dict(cmd="mt_bcast", to=[dict(phone=msg.contact.tel, id=msg.pk) for msg in msgs], msg=broadcast.text))
    """
    commands = []
    current_text = None
    contact_id_pairs = []

    for m in msgs.values("id", "text", "contact_urn__path").order_by("created_on"):
        if m["text"] != current_text and contact_id_pairs:
            commands.append(dict(cmd="mt_bcast", to=contact_id_pairs, msg=current_text))
            contact_id_pairs = []

        current_text = m["text"]
        contact_id_pairs.append(dict(phone=m["contact_urn__path"], id=m["id"]))

    if contact_id_pairs:
        commands.append(dict(cmd="mt_bcast", to=contact_id_pairs, msg=current_text))

    return commands


def get_channel_commands(channel, commands, sync_event=None):
    """
    Generates sync commands for all queued messages on the given channel
    """

    msgs = Msg.objects.filter(status__in=Msg.STATUS_QUEUED, channel=channel, direction=Msg.DIRECTION_OUT)

    if sync_event:
        pending_msgs = sync_event.get_pending_messages()
        retry_msgs = sync_event.get_retry_messages()
        msgs = msgs.exclude(id__in=pending_msgs).exclude(id__in=retry_msgs)

    commands += get_sync_commands(msgs=msgs)

    return commands


def _get_access_token():  # pragma: no cover
    """
    Retrieve a valid access token that can be used to authorize requests.
    """
    credentials = service_account.Credentials.from_service_account_file(
        settings.ANDROID_CREDENTIALS_FILE, scopes=["https://www.googleapis.com/auth/firebase.messaging"]
    )
    request = google.auth.transport.requests.Request()
    credentials.refresh(request)
    return credentials.token


def validate_registration_info(registration_id):  # pragma: no cover
    valid_registration_ids = []

    backoffs = [1, 3, 6]
    while backoffs:
        resp = requests.get(
            f"https://iid.googleapis.com/iid/info/{registration_id}",
            params={"details": "true"},
            headers={
                "Authorization": "Bearer " + _get_access_token(),
                "access_token_auth": "true",
                "Content-Type": "application/json",
            },
        )

        if resp.status_code == 200:
            valid_registration_ids.append(registration_id)
            break
        else:
            time.sleep(backoffs[0])
            backoffs = backoffs[1:]

    return valid_registration_ids


def sync_channel_fcm(registration_id, channel=None):  # pragma: no cover
    fcm_failed = False
    try:
        resp = requests.post(
            f"https://fcm.googleapis.com/v1/projects/{settings.ANDROID_FCM_PROJECT_ID}/messages:send",
            json={"message": {"token": registration_id, "data": {"msg": "sync"}}},
            headers={
                "Authorization": "Bearer " + _get_access_token(),
                "Content-Type": "application/json",
            },
        )

        success = 0
        if resp.status_code == 200:
            resp_json = resp.json()
            success = resp_json.get("success", 0)
            message_id = resp_json.get("message_id", None)
            if message_id:
                success = 1
        if not success:
            fcm_failed = True
    except requests.RequestException:
        fcm_failed = True

    if fcm_failed:
        valid_registration_ids = validate_registration_info(registration_id)

        if registration_id not in valid_registration_ids:
            # this fcm id is invalid now, clear it out
            channel.config.pop(Channel.CONFIG_FCM_ID, None)
            channel.save(update_fields=["config"])


def update_message(msg, cmd):
    """
    Updates a message according to the provided client command
    """

    date = datetime.fromtimestamp(int(cmd["ts"]) // 1000).replace(tzinfo=tzone.utc)
    keyword = cmd["cmd"]
    handled = False

    if keyword == "mt_error":
        msg.status = Msg.STATUS_ERRORED
        handled = True

    elif keyword == "mt_fail":
        msg.status = Msg.STATUS_FAILED
        handled = True

    elif keyword == "mt_sent":
        msg.status = Msg.STATUS_SENT
        msg.sent_on = date
        handled = True

    elif keyword == "mt_dlvd":
        msg.status = Msg.STATUS_DELIVERED
        msg.sent_on = msg.sent_on or date
        handled = True

    msg.save(update_fields=("status", "sent_on"))
    return handled
