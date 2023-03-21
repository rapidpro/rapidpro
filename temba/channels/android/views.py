import base64
import hashlib
import hmac
import time
from datetime import datetime, timedelta

import pytz

from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt

from temba.contacts.models import URN
from temba.msgs.models import Msg
from temba.utils import analytics, json

from ..models import Alert, Channel, SyncEvent
from .claim import UnsupportedAndroidChannelError, get_or_create_channel
from .sync import create_event, create_incoming, get_channel_commands, update_message


@csrf_exempt
def register(request):
    """
    Endpoint for Android devices registering with this server
    """
    if request.method != "POST":
        return HttpResponse(status=500, content=_("POST Required"))

    client_payload = json.loads(force_str(request.body))
    cmds = client_payload["cmds"]

    try:
        # look up a channel with that id
        channel = get_or_create_channel(cmds[0], cmds[1])
        cmd = dict(
            cmd="reg", relayer_claim_code=channel.claim_code, relayer_secret=channel.secret, relayer_id=channel.id
        )
    except UnsupportedAndroidChannelError:
        cmd = dict(cmd="reg", relayer_claim_code="*********", relayer_secret="0" * 64, relayer_id=-1)

    return JsonResponse(dict(cmds=[cmd]))


@csrf_exempt
def sync(request, channel_id):
    start = time.time()

    if request.method != "POST":
        return HttpResponse(status=500, content="POST Required")

    commands = []
    channel = Channel.objects.filter(id=channel_id, is_active=True).first()
    if not channel:
        return JsonResponse(dict(cmds=[dict(cmd="rel", relayer_id=channel_id)]))

    request_time = request.GET.get("ts", "")
    request_signature = force_bytes(request.GET.get("signature", ""))

    if not channel.secret:
        return JsonResponse({"error_id": 4, "error": "Can't sync unclaimed channel", "cmds": []}, status=401)

    # check that the request isn't too old (15 mins)
    now = time.time()
    if abs(now - int(request_time)) > 60 * 15:
        return JsonResponse({"error_id": 3, "error": "Old Request", "cmds": []}, status=401)

    # sign the request
    signature = hmac.new(
        key=force_bytes(str(channel.secret + request_time)), msg=force_bytes(request.body), digestmod=hashlib.sha256
    ).digest()

    # base64 and url sanitize
    signature = base64.urlsafe_b64encode(signature).strip()

    if request_signature != signature:
        return JsonResponse(
            {"error_id": 1, "error": "Invalid signature: '%(request)s'" % {"request": request_signature}, "cmds": []},
            status=401,
        )

    # update our last seen on our channel if we haven't seen this channel in a bit
    if not channel.last_seen or timezone.now() - channel.last_seen > timedelta(minutes=5):
        channel.last_seen = timezone.now()
        channel.save(update_fields=["last_seen"])

    sync_event = None

    # Take the update from the client
    cmds = []
    if request.body:
        body_parsed = json.loads(request.body)

        # all valid requests have to begin with a FCM command
        if "cmds" not in body_parsed or len(body_parsed["cmds"]) < 1 or body_parsed["cmds"][0]["cmd"] != "fcm":
            return JsonResponse({"error_id": 4, "error": "Missing FCM command", "cmds": []}, status=401)

        cmds = body_parsed["cmds"]

    if not channel.org and channel.uuid == cmds[0].get("uuid"):
        # Unclaimed channel with same UUID resend the registration commmands
        cmd = dict(
            cmd="reg", relayer_claim_code=channel.claim_code, relayer_secret=channel.secret, relayer_id=channel.id
        )
        return JsonResponse(dict(cmds=[cmd]))
    elif not channel.org:
        return JsonResponse({"error_id": 4, "error": "Can't sync unclaimed channel", "cmds": []}, status=401)

    unique_calls = set()

    for cmd in cmds:
        handled = False
        extra = None

        if "cmd" in cmd:
            keyword = cmd["cmd"]

            # catchall for commands that deal with a single message
            if "msg_id" in cmd:
                # make sure the negative ids are converted to long
                msg_id = cmd["msg_id"]
                if msg_id < 0:
                    msg_id = 4294967296 + msg_id

                msg = Msg.objects.filter(id=msg_id, org=channel.org).first()
                if msg:
                    if msg.direction == Msg.DIRECTION_OUT:
                        handled = update_message(msg, cmd)
                    else:
                        handled = True

            # creating a new message
            elif keyword == "mo_sms":
                date = datetime.fromtimestamp(int(cmd["ts"]) // 1000).replace(tzinfo=pytz.utc)

                # it is possible to receive spam SMS messages from no number on some carriers
                tel = cmd["phone"] if cmd["phone"] else "empty"
                try:
                    urn = URN.normalize(URN.from_tel(tel), channel.country.code)

                    if "msg" in cmd:
                        msg = create_incoming(channel.org, channel, urn, cmd["msg"], date)
                        extra = dict(msg_id=msg.id)
                except ValueError:
                    pass

                handled = True

            # phone event
            elif keyword == "call":
                call_tuple = (cmd["ts"], cmd["type"], cmd["phone"])
                date = datetime.fromtimestamp(int(cmd["ts"]) // 1000).replace(tzinfo=pytz.utc)

                duration = 0
                if cmd["type"] != "miss":
                    duration = cmd["dur"]

                # Android sometimes will pass us a call from an 'unknown number', which is null
                # ignore these events on our side as they have no purpose and break a lot of our
                # assumptions
                if cmd["phone"] and call_tuple not in unique_calls:
                    urn = URN.from_tel(cmd["phone"])
                    try:
                        create_event(channel, urn, cmd["type"], date, extra={"duration": duration})
                    except ValueError:
                        # in some cases Android passes us invalid URNs, in those cases just ignore them
                        pass

                    unique_calls.add(call_tuple)
                handled = True

            elif keyword == "fcm":
                # update our fcm and uuid

                config = channel.config
                config.update({Channel.CONFIG_FCM_ID: cmd["fcm_id"]})
                channel.config = config
                channel.uuid = cmd.get("uuid", None)
                channel.save(update_fields=["uuid", "config"])

                # no acking the fcm
                handled = False

            elif keyword == "reset":
                # release this channel
                channel.release(channel.modified_by, trigger_sync=False)
                channel.save()

                # ack that things got handled
                handled = True

            elif keyword == "status":
                sync_event = SyncEvent.create(channel, cmd, cmds)
                Alert.check_power_alert(sync_event)

                # tell the channel to update its org if this channel got moved
                if channel.org and "org_id" in cmd and channel.org.pk != cmd["org_id"]:
                    commands.append(dict(cmd="claim", org_id=channel.org.pk))

                # we don't ack status messages since they are always included
                handled = False

        # is this something we can ack?
        if "p_id" in cmd and handled:
            ack = dict(p_id=cmd["p_id"], cmd="ack")
            if extra:
                ack["extra"] = extra

            commands.append(ack)

    outgoing_cmds = get_channel_commands(channel, commands, sync_event)
    result = dict(cmds=outgoing_cmds)

    if sync_event:
        sync_event.outgoing_command_count = len([_ for _ in outgoing_cmds if _["cmd"] != "ack"])
        sync_event.save()

    # keep track of how long a sync takes
    analytics.gauges({"temba.relayer_sync": time.time() - start})

    return JsonResponse(result)
