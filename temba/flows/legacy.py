import logging
import time

import requests

from django.conf import settings

from temba.utils import json, prepped_request_to_str
from temba.utils.http import http_headers

logger = logging.getLogger(__name__)


def call_webhook(run, webhook_url, ruleset, msg, action="POST", resthook=None, headers=None):
    from temba.api.models import WebHookEvent, WebHookResult
    from temba.flows.models import Flow

    flow = run.flow
    contact = run.contact
    org = flow.org
    channel = msg.channel if msg else None
    contact_urn = msg.contact_urn if (msg and msg.contact_urn) else contact.get_urn()

    contact_dict = dict(uuid=contact.uuid, name=contact.name)
    if contact_urn:
        contact_dict["urn"] = contact_urn.urn

    post_data = {
        "contact": contact_dict,
        "flow": dict(name=flow.name, uuid=flow.uuid, revision=flow.revisions.order_by("revision").last().revision),
        "path": run.path,
        "results": run.results,
        "run": dict(uuid=str(run.uuid), created_on=run.created_on.isoformat()),
    }

    if msg and msg.id > 0:
        post_data["input"] = dict(
            urn=msg.contact_urn.urn if msg.contact_urn else None, text=msg.text, attachments=(msg.attachments or [])
        )

    if channel:
        post_data["channel"] = dict(name=channel.name, uuid=channel.uuid)

    if not action:  # pragma: needs cover
        action = "POST"

    if resthook:
        WebHookEvent.objects.create(org=org, data=post_data, action=action, resthook=resthook)

    status_code = -1
    message = "None"
    body = None
    request = ""

    start = time.time()

    # webhook events fire immediately since we need the results back
    try:
        # no url, bail!
        if not webhook_url:
            raise ValueError("No webhook_url specified, skipping send")

        # only send webhooks when we are configured to, otherwise fail
        if settings.SEND_WEBHOOKS:
            requests_headers = http_headers(extra=headers)

            s = requests.Session()

            # some hosts deny generic user agents, use Temba as our user agent
            if action == "GET":
                prepped = requests.Request("GET", webhook_url, headers=requests_headers).prepare()
            else:
                requests_headers["Content-type"] = "application/json"
                prepped = requests.Request(
                    "POST", webhook_url, data=json.dumps(post_data), headers=requests_headers
                ).prepare()

            request = prepped_request_to_str(prepped)
            response = s.send(prepped, timeout=10)
            body = response.text
            if body:
                body = body.strip()
            status_code = response.status_code

        else:
            print("!! Skipping WebHook send, SEND_WEBHOOKS set to False")
            body = "Skipped actual send"
            status_code = 200

        if ruleset:
            run.update_fields({Flow.label_to_slug(ruleset.label): body}, do_save=False)
        new_extra = {}

        # process the webhook response
        try:
            response_json = json.loads(body)

            # only update if we got a valid JSON dictionary or list
            if not isinstance(response_json, dict) and not isinstance(response_json, list):
                raise ValueError("Response must be a JSON dictionary or list, ignoring response.")

            new_extra = response_json
            message = "Webhook called successfully."
        except ValueError:
            message = "Response must be a JSON dictionary, ignoring response."

        run.update_fields(new_extra)

        if not (200 <= status_code < 300):
            message = "Got non 200 response (%d) from webhook." % response.status_code
            raise ValueError("Got non 200 response (%d) from webhook." % response.status_code)

    except (requests.ReadTimeout, ValueError) as e:
        message = f"Error calling webhook: {str(e)}"

    except Exception as e:
        logger.error(f"Could not trigger flow webhook: {str(e)}", exc_info=True)

        message = "Error calling webhook: %s" % str(e)

    finally:
        # make sure our message isn't too long
        if message:
            message = message[:255]

        if body is None:
            body = message

        request_time = (time.time() - start) * 1000

        contact = None
        if run:
            contact = run.contact

        result = WebHookResult.objects.create(
            contact=contact,
            url=webhook_url,
            status_code=status_code,
            response=body,
            request=request,
            request_time=request_time,
            org=run.org,
        )

    return result
