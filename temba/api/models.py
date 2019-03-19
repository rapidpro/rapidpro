import hmac
import logging
import time
import uuid
from datetime import timedelta
from hashlib import sha1
from urllib.parse import urlencode

import requests
from rest_framework.permissions import BasePermission
from smartmin.models import SmartModel

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import Channel, ChannelEvent
from temba.contacts.models import TEL_SCHEME
from temba.flows.models import Flow, FlowRun
from temba.orgs.models import Org
from temba.utils import json, on_transaction_commit, prepped_request_to_str
from temba.utils.cache import get_cacheable_attr
from temba.utils.http import http_headers
from temba.utils.models import JSONAsTextField

logger = logging.getLogger(__name__)


class APIPermission(BasePermission):
    """
    Verifies that the user has the permission set on the endpoint view
    """

    def has_permission(self, request, view):

        if getattr(view, "permission", None):
            # no anon access to API endpoints
            if request.user.is_anonymous:
                return False

            org = request.user.get_org()

            if request.auth:
                role_group = request.auth.role
                allowed_roles = APIToken.get_allowed_roles(org, request.user)

                # check that user is still allowed to use the token's role
                if role_group not in allowed_roles:
                    return False
            elif org:
                # user may not have used token authentication
                role_group = org.get_user_org_group(request.user)
            else:
                return False

            codename = view.permission.split(".")[-1]
            return role_group.permissions.filter(codename=codename).exists()

        else:  # pragma: no cover
            return True


class SSLPermission(BasePermission):  # pragma: no cover
    """
    Verifies that the request used SSL if that is required
    """

    def has_permission(self, request, view):
        if getattr(settings, "SESSION_COOKIE_SECURE", False):
            return request.is_secure()
        else:
            return True


class Resthook(SmartModel):
    """
    Represents a hook that a user creates on an organization. Outside apps can integrate by subscribing
    to this particular resthook.
    """

    org = models.ForeignKey(
        Org,
        on_delete=models.PROTECT,
        related_name="resthooks",
        help_text=_("The organization this resthook belongs to"),
    )

    slug = models.SlugField(help_text=_("A simple label for this event"))

    @classmethod
    def get_or_create(cls, org, slug, user):
        """
        Looks up (or creates) the resthook for the passed in org and slug
        """
        slug = slug.lower().strip()
        resthook = Resthook.objects.filter(is_active=True, org=org, slug=slug).first()
        if not resthook:
            resthook = Resthook.objects.create(org=org, slug=slug, created_by=user, modified_by=user)

        return resthook

    def get_subscriber_urls(self):
        return [s.target_url for s in self.subscribers.filter(is_active=True).order_by("created_on")]

    def add_subscriber(self, url, user):
        subscriber = self.subscribers.create(target_url=url, created_by=user, modified_by=user)
        self.modified_by = user
        self.save(update_fields=["modified_on", "modified_by"])
        return subscriber

    def remove_subscriber(self, url, user):
        now = timezone.now()
        self.subscribers.filter(target_url=url, is_active=True).update(
            is_active=False, modified_on=now, modified_by=user
        )
        self.modified_by = user
        self.save(update_fields=["modified_on", "modified_by"])

    def release(self, user):
        # release any active subscribers
        for s in self.subscribers.filter(is_active=True):
            s.release(user)

        # then ourselves
        self.is_active = False
        self.modified_by = user
        self.save(update_fields=["is_active", "modified_on", "modified_by"])

    def as_select2(self):
        return dict(text=self.slug, id=self.slug)

    def __str__(self):  # pragma: needs cover
        return str(self.slug)


class ResthookSubscriber(SmartModel):
    """
    Represents a subscriber on a specific resthook within one of our flows.
    """

    resthook = models.ForeignKey(
        Resthook, on_delete=models.PROTECT, related_name="subscribers", help_text=_("The resthook being subscribed to")
    )

    target_url = models.URLField(help_text=_("The URL that we will call when our ruleset is reached"))

    def as_json(self):  # pragma: needs cover
        return dict(id=self.id, resthook=self.resthook.slug, target_url=self.target_url, created_on=self.created_on)

    def release(self, user):
        self.is_active = False
        self.modified_by = user
        self.save(update_fields=["is_active", "modified_on", "modified_by"])

        # update our parent as well
        self.resthook.modified_by = user
        self.resthook.save(update_fields=["modified_on", "modified_by"])


class WebHookEvent(models.Model):
    """
    Represents an event that needs to be sent to the web hook for a channel.
    """

    TYPE_SMS_RECEIVED = "mo_sms"
    TYPE_SMS_SENT = "mt_sent"
    TYPE_SMS_DELIVERED = "mt_dlvd"
    TYPE_SMS_FAIL = "mt_fail"
    TYPE_RELAYER_ALARM = "alarm"
    TYPE_FLOW = "flow"
    TYPE_CATEGORIZE = "categorize"

    TYPE_CHOICES = (
        (TYPE_SMS_RECEIVED, "Incoming SMS Message"),
        (TYPE_SMS_SENT, "Outgoing SMS Sent"),
        (TYPE_SMS_DELIVERED, "Outgoing SMS Delivered to Recipient"),
        (TYPE_SMS_FAIL, "Outgoing SMS Failed to be Delivered to Recipient"),
        (ChannelEvent.TYPE_CALL_OUT, "Outgoing Call"),
        (ChannelEvent.TYPE_CALL_OUT_MISSED, "Missed Outgoing Call"),
        (ChannelEvent.TYPE_CALL_IN, "Incoming Call"),
        (ChannelEvent.TYPE_CALL_IN_MISSED, "Missed Incoming Call"),
        (TYPE_RELAYER_ALARM, "Channel Alarm"),
        (TYPE_FLOW, "Flow Step Reached"),
        (TYPE_CATEGORIZE, "Flow Categorization"),
    )

    STATUS_PENDING = "P"
    STATUS_COMPLETE = "C"
    STATUS_FAILED = "F"
    STATUS_ERRORED = "E"

    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_COMPLETE, "Complete"),
        (STATUS_ERRORED, "Errored"),
        (STATUS_FAILED, "Failed"),
    )

    # the organization this event is tied to
    org = models.ForeignKey(Org, on_delete=models.PROTECT)

    # the resthook this event is for if any
    resthook = models.ForeignKey(Resthook, on_delete=models.PROTECT, null=True)

    # the status of this event
    status = models.CharField(max_length=1, choices=STATUS_CHOICES, default="P")

    # the flow run this event is associated with if any
    run = models.ForeignKey(FlowRun, on_delete=models.PROTECT, related_name="webhook_events", null=True)

    # the channel this event is for if any
    channel = models.ForeignKey(Channel, on_delete=models.PROTECT, null=True, blank=True)

    # the type of event
    event = models.CharField(max_length=16, choices=TYPE_CHOICES)

    # the data that would have been POSTed to this event
    data = JSONAsTextField(default=dict)

    # how many times we have tried to deliver this event
    try_count = models.IntegerField(default=0)

    # the next time we will attempt this event if any
    next_attempt = models.DateTimeField(null=True, blank=True)

    # the method for our request
    action = models.CharField(max_length=8, default="POST")

    # when this event was created
    created_on = models.DateTimeField(default=timezone.now, editable=False, blank=True)

    @classmethod
    def get_recent_errored(cls, org):
        past_hour = timezone.now() - timedelta(hours=1)
        return cls.objects.filter(
            org=org, status__in=(cls.STATUS_FAILED, cls.STATUS_ERRORED), created_on__gte=past_hour
        )

    def fire(self):
        # start our task with this event id
        from .tasks import deliver_event_task

        on_transaction_commit(lambda: deliver_event_task.delay(self.id))

    @classmethod
    def trigger_flow_webhook(cls, run, webhook_url, ruleset, msg, action="POST", resthook=None, headers=None):

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
                urn=msg.contact_urn.urn if msg.contact_urn else None,
                text=msg.text,
                attachments=(msg.attachments or []),
            )

        if channel:
            post_data["channel"] = dict(name=channel.name, uuid=channel.uuid)

        if not action:  # pragma: needs cover
            action = "POST"

        webhook_event = cls.objects.create(
            org=org,
            event=cls.TYPE_FLOW,
            channel=channel,
            data=post_data,
            run=run,
            try_count=1,
            action=action,
            resthook=resthook,
        )

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

            if 200 <= status_code < 300:
                webhook_event.status = cls.STATUS_COMPLETE
            else:
                webhook_event.status = cls.STATUS_FAILED
                message = "Got non 200 response (%d) from webhook." % response.status_code
                raise ValueError("Got non 200 response (%d) from webhook." % response.status_code)

        except (requests.ReadTimeout, ValueError) as e:
            webhook_event.status = cls.STATUS_FAILED
            message = f"Error calling webhook: {str(e)}"

        except Exception as e:
            logger.error(f"Could not trigger flow webhook: {str(e)}", exc_info=True)

            webhook_event.status = cls.STATUS_FAILED
            message = "Error calling webhook: %s" % str(e)

        finally:
            webhook_event.save(update_fields=("status",))

            # make sure our message isn't too long
            if message:
                message = message[:255]

            if body is None:
                body = message

            request_time = (time.time() - start) * 1000

            contact = None
            if webhook_event.run:
                contact = webhook_event.run.contact

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

    @classmethod
    def trigger_sms_event(cls, event, msg, time):
        if not msg.channel:
            return

        org = msg.org

        # no-op if no webhook configured
        if not org or not org.get_webhook_url():
            return

        # if the org doesn't care about this type of message, ignore it
        if (
            (event == cls.TYPE_SMS_RECEIVED and not org.is_notified_of_mo_sms())
            or (event == cls.TYPE_SMS_SENT and not org.is_notified_of_mt_sms())
            or (event == cls.TYPE_SMS_DELIVERED and not org.is_notified_of_mt_sms())
        ):
            return

        json_time = time.strftime("%Y-%m-%dT%H:%M:%S.%f")
        data = dict(
            sms=msg.id,
            phone=msg.contact.get_urn_display(org=org, scheme=TEL_SCHEME, formatted=False),
            contact=msg.contact.uuid,
            contact_name=msg.contact.name,
            urn=str(msg.contact_urn),
            text=msg.text,
            attachments=[a.url for a in msg.get_attachments()],
            time=json_time,
            status=msg.status,
            direction=msg.direction,
        )

        hook_event = cls.objects.create(org=org, channel=msg.channel, event=event, data=data)
        hook_event.fire()
        return hook_event

    @classmethod
    def trigger_channel_alarm(cls, sync_event):
        channel = sync_event.channel
        org = channel.org

        # no-op if no webhook configured
        if not org or not org.get_webhook_url():  # pragma: no cover
            return

        if not org.is_notified_of_alarms():
            return

        json_time = channel.last_seen.strftime("%Y-%m-%dT%H:%M:%S.%f")
        data = dict(
            channel=channel.pk,
            channel_uuid=channel.uuid,
            power_source=sync_event.power_source,
            power_status=sync_event.power_status,
            power_level=sync_event.power_level,
            network_type=sync_event.network_type,
            pending_message_count=sync_event.pending_message_count,
            retry_message_count=sync_event.retry_message_count,
            last_seen=json_time,
        )

        hook_event = cls.objects.create(org=org, channel=channel, event=cls.TYPE_RELAYER_ALARM, data=data)
        hook_event.fire()
        return hook_event

    def deliver(self):
        from .v1.serializers import MsgCreateSerializer

        start = time.time()

        # create our post parameters
        post_data = self.data
        post_data["event"] = self.event
        post_data["relayer"] = self.channel.pk if self.channel else ""
        post_data["channel"] = self.channel.pk if self.channel else ""
        post_data["relayer_phone"] = self.channel.address if self.channel else ""

        # look up the endpoint for this channel
        result = dict(url=self.org.get_webhook_url(), data=urlencode(post_data, doseq=True))

        if not self.org.get_webhook_url():  # pragma: no cover
            result["status_code"] = 0
            result["response"] = "No webhook registered for this org, ignoring event"
            self.status = self.STATUS_FAILED
            self.next_attempt = None
            return result

        # get our org user
        user = self.org.get_user()

        # no user?  we shouldn't be doing webhooks shtuff
        if not user:
            result["status_code"] = 0
            result["response"] = "No active user for this org, ignoring event"
            self.status = self.STATUS_FAILED
            self.next_attempt = None
            return result

        # make the request
        try:
            if not settings.SEND_WEBHOOKS:  # pragma: no cover
                raise Exception("!! Skipping WebHook send, SEND_WEBHOOKS set to False")

            headers = http_headers(extra=self.org.get_webhook_headers())

            s = requests.Session()
            prepped = requests.Request("POST", self.org.get_webhook_url(), data=post_data, headers=headers).prepare()
            result["url"] = prepped.url
            result["request"] = prepped_request_to_str(prepped)
            r = s.send(prepped, timeout=5)

            result["status_code"] = r.status_code
            result["body"] = r.text.strip()

            r.raise_for_status()

            # any 200 code is ok by us
            self.status = self.STATUS_COMPLETE
            result["request_time"] = (time.time() - start) * 1000

            # read our body if we have one
            if result["body"]:
                try:
                    data = r.json()
                    serializer = MsgCreateSerializer(data=data, user=user, org=self.org)

                    if serializer.is_valid():
                        result["serializer"] = serializer

                except ValueError:
                    pass

        except Exception:
            # we had an error, log it
            self.status = self.STATUS_ERRORED
            result["request_time"] = time.time() - start

        # if we had an error of some kind, schedule a retry for five minutes from now
        self.try_count += 1

        if self.status == self.STATUS_ERRORED:
            if self.try_count < 3:
                self.next_attempt = timezone.now() + timedelta(minutes=5)
            else:
                self.next_attempt = None
                self.status = "F"
        else:
            self.next_attempt = None

        return result

    def release(self):
        self.delete()

    def __str__(self):  # pragma: needs cover
        return "WebHookEvent[%s:%d] %s" % (self.event, self.pk, self.data)


class WebHookResult(models.Model):
    """
    Represents the result of trying to deliver an event to a web hook
    """

    # the url this result is for
    url = models.TextField(null=True, blank=True)

    # the body of the request
    request = models.TextField(null=True, blank=True)

    # the status code returned (set to 503 for connection errors)
    status_code = models.IntegerField()

    # the body of the response
    response = models.TextField(null=True, blank=True)

    # how long the request took to return in milliseconds
    request_time = models.IntegerField(null=True)

    # the contact associated with this result (if any)
    contact = models.ForeignKey(
        "contacts.Contact", on_delete=models.PROTECT, null=True, related_name="webhook_results"
    )

    # the org this result belongs to
    org = models.ForeignKey("orgs.Org", on_delete=models.PROTECT, related_name="webhook_results")

    # when this result was created
    created_on = models.DateTimeField(default=timezone.now, editable=False, blank=True)

    @classmethod
    def record_result(cls, event, result):
        # save our event
        event.save()

        # if our serializer was valid, save it, this will send the message out
        serializer = result.get("serializer", None)
        if serializer and serializer.is_valid():
            serializer.save()

        cls.objects.create(
            url=result["url"],
            request=result.get("request"),
            status_code=result.get("status_code", 503),
            response=result.get("body"),
            request_time=result.get("request_time", None),
            org=event.org,
        )

    @property
    def is_success(self):
        return 200 <= self.status_code < 300

    def release(self):
        self.delete()


class APIToken(models.Model):
    """
    Our API token, ties in orgs
    """

    CODE_TO_ROLE = {"A": "Administrators", "E": "Editors", "S": "Surveyors"}

    ROLE_GRANTED_TO = {
        "Administrators": ("Administrators",),
        "Editors": ("Administrators", "Editors"),
        "Surveyors": ("Administrators", "Editors", "Surveyors"),
    }

    is_active = models.BooleanField(default=True)

    key = models.CharField(max_length=40, primary_key=True)

    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="api_tokens")

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="api_tokens")

    created = models.DateTimeField(auto_now_add=True)

    role = models.ForeignKey(Group, on_delete=models.PROTECT)

    @classmethod
    def get_or_create(cls, org, user, role=None, refresh=False):
        """
        Gets or creates an API token for this user
        """
        if not role:
            role = cls.get_default_role(org, user)

        if not role:
            raise ValueError("User '%s' has no suitable role for API usage" % str(user))
        elif role.name not in cls.ROLE_GRANTED_TO:
            raise ValueError("Role %s is not valid for API usage" % role.name)

        tokens = cls.objects.filter(is_active=True, user=user, org=org, role=role)

        # if we are refreshing the token, clear existing ones
        if refresh and tokens:
            for token in tokens:
                token.release()
            tokens = None

        if not tokens:
            token = cls.objects.create(user=user, org=org, role=role)
        else:
            token = tokens.first()

        return token

    @classmethod
    def get_orgs_for_role(cls, user, role):
        """
        Gets all the orgs the user can access the API with the given role
        """
        user_query = Q()
        for user_group in cls.ROLE_GRANTED_TO.get(role.name):
            user_query |= Q(**{user_group.lower(): user})

        return Org.objects.filter(user_query)

    @classmethod
    def get_default_role(cls, org, user):
        """
        Gets the default API role for the given user
        """
        group = org.get_user_org_group(user)

        if not group or group.name not in cls.ROLE_GRANTED_TO:  # don't allow creating tokens for Viewers group etc
            return None

        return group

    @classmethod
    def get_allowed_roles(cls, org, user):
        """
        Gets all of the allowed API roles for the given user
        """
        group = org.get_user_org_group(user)

        if group:
            role_names = []
            for role_name, granted_to in cls.ROLE_GRANTED_TO.items():
                if group.name in granted_to:
                    role_names.append(role_name)

            return Group.objects.filter(name__in=role_names)
        else:
            return []

    @classmethod
    def get_role_from_code(cls, code):
        role = cls.CODE_TO_ROLE.get(code)
        return Group.objects.get(name=role) if role else None

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = self.generate_key()
        return super().save(*args, **kwargs)

    def generate_key(self):
        unique = uuid.uuid4()
        return hmac.new(unique.bytes, digestmod=sha1).hexdigest()

    def release(self):
        self.is_active = False
        self.save()

    def __str__(self):
        return self.key


def get_or_create_api_token(user):
    """
    Gets or creates an API token for this user. If user doen't have access to the API, this returns None.
    """
    org = user.get_org()
    if not org:
        org = Org.get_org(user)

    if org:
        try:
            token = APIToken.get_or_create(org, user)
            return token.key
        except ValueError:
            pass

    return None


def api_token(user):
    """
    Cached property access to a user's lazily-created API token
    """
    return get_cacheable_attr(user, "__api_token", lambda: get_or_create_api_token(user))


User.api_token = property(api_token)


def get_api_user():
    """
    Returns a user that can be used to associate events created by the API service
    """
    user = User.objects.filter(username="api")
    if user:
        return user[0]
    else:
        user = User.objects.create_user("api", "code@temba.com")
        user.groups.add(Group.objects.get(name="Service Users"))
        return user
