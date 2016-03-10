from __future__ import absolute_import, unicode_literals

import hmac
import json
import requests
import uuid

from datetime import timedelta
from django.db.models import Q
from django.conf import settings
from django.core.urlresolvers import reverse
from django.contrib.auth.models import User, Group
from django.db import models
from django.utils import timezone
from hashlib import sha1
from rest_framework.permissions import BasePermission
from smartmin.models import SmartModel
from temba.contacts.models import TEL_SCHEME
from temba.orgs.models import Org
from temba.channels.models import Channel, TEMBA_HEADERS
from temba.msgs.models import Call
from temba.utils import datetime_to_str, prepped_request_to_str
from temba.utils.cache import get_cacheable_attr
from urllib import urlencode

PENDING = 'P'
COMPLETE = 'C'
FAILED = 'F'
ERRORED = 'E'

STATUS_CHOICES = ((PENDING, "Pending"),
                  (COMPLETE, "Complete"),
                  (ERRORED, "Errored"),
                  (FAILED, "Failed"))

SMS_RECEIVED = 'mo_sms'
SMS_SENT = 'mt_sent'
SMS_DELIVERED = 'mt_dlvd'
SMS_FAIL = 'mt_fail'

RELAYER_ALARM = 'alarm'

FLOW = 'flow'
CATEGORIZE = 'categorize'

EVENT_CHOICES = ((SMS_RECEIVED, "Incoming SMS Message"),
                 (SMS_SENT, "Outgoing SMS Sent"),
                 (SMS_DELIVERED, "Outgoing SMS Delivered to Recipient"),
                 (Call.TYPE_OUT, "Outgoing Call"),
                 (Call.TYPE_OUT_MISSED, "Missed Outgoing Call"),
                 (Call.TYPE_IN, "Incoming Call"),
                 (Call.TYPE_IN_MISSED, "Missed Incoming Call"),
                 (RELAYER_ALARM, "Channel Alarm"),
                 (FLOW, "Flow Step Reached"),
                 (CATEGORIZE, "Flow Categorization"))


class ApiPermission(BasePermission):
    def has_permission(self, request, view):

        if getattr(view, 'permission', None):

            if request.user.is_anonymous():
                return False

            # try to determine group from api token
            if request.auth:
                group = request.auth.role
            # otherwise lean on the logged in user
            else:
                org = request.user.get_org()
                group = org.get_user_org_group(request.user) if org else None

            # if we have a group, check its permissions
            if group:
                codename = view.permission.split(".")[-1]
                return group.permissions.filter(codename=codename)

            return False

        else:  # pragma: no cover
            return True


class SSLPermission(BasePermission):  # pragma: no cover
    def has_permission(self, request, view):
        if getattr(settings, 'SESSION_COOKIE_SECURE', False):
            return request.is_secure()
        else:
            return True


class WebHookEvent(SmartModel):
    """
    Represents an event that needs to be sent to the web hook for a channel.
    """
    org = models.ForeignKey(Org,
                            help_text="The organization that this event was triggered for")
    status = models.CharField(max_length=1, choices=STATUS_CHOICES, default='P',
                              help_text="The state this event is currently in")
    channel = models.ForeignKey(Channel, null=True, blank=True,
                                help_text="The channel that this event is relating to")
    event = models.CharField(max_length=16, choices=EVENT_CHOICES,
                             help_text="The event type for this event")
    data = models.TextField(help_text="The JSON encoded data that will be POSTED to the web hook")

    try_count = models.IntegerField(default=0,
                                    help_text="The number of times this event has been tried")
    next_attempt = models.DateTimeField(null=True, blank=True,
                                        help_text="When this event will be retried")

    action = models.CharField(max_length=8, default='POST', help_text='What type of HTTP event is it')

    def fire(self):
        # start our task with this event id
        from .tasks import deliver_event_task
        deliver_event_task.delay(self.id)

    @classmethod
    def trigger_flow_event(cls, webhook_url, flow, run, node_uuid, contact, event, action='POST'):
        org = flow.org
        api_user = get_api_user()

        # no-op if no webhook configured
        if not webhook_url:
            return

        json_time = datetime_to_str(timezone.now())

        # get the results for this contact
        results = flow.get_results(contact)
        values = []

        if results and results[0]:
            values = results[0]['values']
            for value in values:
                value['time'] = datetime_to_str(value['time'])
                value['value'] = unicode(value['value'])

        # if the action is on the first node
        # we might not have an sms (or channel) yet
        channel = None
        text = None

        if event:
            text = event.text
            channel = event.channel

        if channel:
            channel_id = channel.pk
        else:
            channel_id = -1

        steps = []
        for step in run.steps.all().order_by('arrived_on'):
            steps.append(dict(type=step.step_type,
                              node=step.step_uuid,
                              arrived_on=datetime_to_str(step.arrived_on),
                              left_on=datetime_to_str(step.left_on),
                              text=step.get_text(),
                              value=step.rule_value))

        data = dict(channel=channel_id,
                    relayer=channel_id,
                    flow=flow.id,
                    run=run.id,
                    text=text,
                    step=unicode(node_uuid),
                    phone=contact.get_urn_display(org=org, scheme=TEL_SCHEME, full=True),
                    values=json.dumps(values),
                    steps=json.dumps(steps),
                    time=json_time)

        if not action:
            action = 'POST'

        webhook_event = WebHookEvent.objects.create(org=org,
                                                    event=FLOW,
                                                    channel=channel,
                                                    data=json.dumps(data),
                                                    try_count=1,
                                                    action=action,
                                                    created_by=api_user,
                                                    modified_by=api_user)

        status_code = -1
        message = "None"
        body = None

        # webhook events fire immediately since we need the results back
        try:
            # only send webhooks when we are configured to, otherwise fail
            if not settings.SEND_WEBHOOKS:
                raise Exception("!! Skipping WebHook send, SEND_WEBHOOKS set to False")

            # some hosts deny generic user agents, use Temba as our user agent
            if action == 'GET':
                response = requests.get(webhook_url, headers=TEMBA_HEADERS, timeout=10)
            else:
                response = requests.post(webhook_url, data=data, headers=TEMBA_HEADERS, timeout=10)

            response_text = response.text
            body = response.text
            status_code = response.status_code

            if response.status_code == 200 or response.status_code == 201:
                try:
                    response_json = json.loads(response_text)

                    # only update if we got a valid JSON dictionary or list
                    if not isinstance(response_json, dict) and not isinstance(response_json, list):
                        raise ValueError("Response must be a JSON dictionary or list, ignoring response.")

                    run.update_fields(response_json)
                    message = "Webhook called successfully."
                except ValueError as e:
                    message = "Response must be a JSON dictionary, ignoring response."

                webhook_event.status = COMPLETE
            else:
                webhook_event.status = FAILED
                message = "Got non 200 response (%d) from webhook." % response.status_code
                raise Exception("Got non 200 response (%d) from webhook." % response.status_code)

        except Exception as e:
            import traceback
            traceback.print_exc()

            webhook_event.status = FAILED
            message = "Error calling webhook: %s" % unicode(e)

        finally:
            webhook_event.save()
            result = WebHookResult.objects.create(event=webhook_event,
                                                  url=webhook_url,
                                                  status_code=status_code,
                                                  body=body,
                                                  message=message,
                                                  data=urlencode(data, doseq=True),
                                                  created_by=api_user,
                                                  modified_by=api_user)

            # if this is a test contact, add an entry to our action log
            if run.contact.is_test:
                from temba.flows.models import ActionLog
                log_txt = "Triggered <a href='%s' target='_log'>webhook event</a> - %d" % (reverse('api.log_read', args=[webhook_event.pk]), status_code)
                ActionLog.create(run, log_txt, safe=True)

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
        if (event == 'mo_sms' and not org.is_notified_of_mo_sms()) or \
            (event == 'mt_sent' and not org.is_notified_of_mt_sms()) or \
            (event == 'mt_dlvd' and not org.is_notified_of_mt_sms()):
            return

        api_user = get_api_user()

        json_time = time.strftime('%Y-%m-%dT%H:%M:%S.%f')
        data = dict(sms=msg.pk,
                    phone=msg.contact.get_urn_display(org=org, scheme=TEL_SCHEME, full=True),
                    text=msg.text,
                    time=json_time,
                    status=msg.status,
                    direction=msg.direction)

        hook_event = WebHookEvent.objects.create(org=org,
                                                 channel=msg.channel,
                                                 event=event,
                                                 data=json.dumps(data),
                                                 created_by=api_user,
                                                 modified_by=api_user)
        hook_event.fire()
        return hook_event

    @classmethod
    def trigger_call_event(cls, call):
        if not call.channel:
            return

        org = call.channel.org

        # no-op if no webhook configured
        if not org or not org.get_webhook_url():
            return

        event = call.call_type

        # if the org doesn't care about this type of message, ignore it
        if (event == 'mt_call' and not org.is_notified_of_mt_call()) or \
           (event == 'mt_miss' and not org.is_notified_of_mt_call()) or \
           (event == 'mo_call' and not org.is_notified_of_mo_call()) or \
           (event == 'mo_miss' and not org.is_notified_of_mo_call()):
            return

        api_user = get_api_user()

        json_time = call.time.strftime('%Y-%m-%dT%H:%M:%S.%f')
        data = dict(call=call.pk,
                    phone=call.contact.get_urn_display(org=org, scheme=TEL_SCHEME, full=True),
                    duration=call.duration,
                    time=json_time)
        hook_event = WebHookEvent.objects.create(org=org,
                                                 channel=call.channel,
                                                 event=event,
                                                 data=json.dumps(data),
                                                 created_by=api_user,
                                                 modified_by=api_user)
        hook_event.fire()
        return hook_event

    @classmethod
    def trigger_channel_alarm(cls, sync_event):
        channel = sync_event.channel
        org = channel.org

        # no-op if no webhook configured
        if not org or not org.get_webhook_url(): # pragma: no cover
            return

        if not org.is_notified_of_alarms():
            return

        api_user = get_api_user()

        json_time = channel.last_seen.strftime('%Y-%m-%dT%H:%M:%S.%f')
        data = dict(channel=channel.pk,
                    power_source=sync_event.power_source,
                    power_status=sync_event.power_status,
                    power_level=sync_event.power_level,
                    network_type=sync_event.network_type,
                    pending_message_count=sync_event.pending_message_count,
                    retry_message_count=sync_event.retry_message_count,
                    last_seen=json_time)

        hook_event = WebHookEvent.objects.create(org=org,
                                                 channel=channel,
                                                 event=RELAYER_ALARM,
                                                 data=json.dumps(data),
                                                 created_by=api_user,
                                                 modified_by=api_user)
        hook_event.fire()
        return hook_event

    def deliver(self):
        from .v1.serializers import MsgCreateSerializer

        # create our post parameters
        post_data = json.loads(self.data)
        post_data['event'] = self.event
        post_data['relayer'] = self.channel.pk
        post_data['channel'] = self.channel.pk
        post_data['relayer_phone'] = self.channel.address

        # look up the endpoint for this channel
        result = dict(url=self.org.get_webhook_url(), data=urlencode(post_data, doseq=True))

        if not self.org.get_webhook_url(): # pragma: no cover
            result['status_code'] = 0
            result['message'] = "No webhook registered for this org, ignoring event"
            self.status = FAILED
            self.next_attempt = None
            return result

        # get our org user
        user = self.org.get_user()

        # no user?  we shouldn't be doing webhooks shtuff
        if not user:
            result['status_code'] = 0
            result['message'] = "No active user for this org, ignoring event"
            self.status = FAILED
            self.next_attempt = None
            return result

        # make the request
        try:
            if not settings.SEND_WEBHOOKS:
                raise Exception("!! Skipping WebHook send, SEND_WEBHOOKS set to False")

            # some hosts deny generic user agents, use Temba as our user agent
            headers = TEMBA_HEADERS.copy()

            # also include any user-defined headers
            headers.update(self.org.get_webhook_headers())

            s = requests.Session()
            prepped = requests.Request('POST', self.org.get_webhook_url(),
                                       data=post_data,
                                       headers=headers).prepare()
            result['url'] = prepped.url
            result['request'] = prepped_request_to_str(prepped)
            r = s.send(prepped)

            result['status_code'] = r.status_code
            result['body'] = r.text.strip()

            r.raise_for_status()

            # any 200 code is ok by us
            self.status = COMPLETE
            result['message'] = "Event delivered successfully."

            # read our body if we have one
            if result['body']:
                try:
                    data = r.json()
                    serializer = MsgCreateSerializer(data=data, user=user, org=self.org)

                    if serializer.is_valid():
                        result['serializer'] = serializer
                        obj = serializer.object
                        result['message'] = "SMS message to %d recipient(s) with text: '%s'" % (len(obj.contacts), obj.text)
                    else:
                        errors = serializer.errors
                        result['message'] = "Event delivered successfully, ignoring response body, wrong format: %s" % \
                                            ",".join("%s: %s" % (_, ",".join(errors[_])) for _ in errors.keys())

                except Exception as e:
                    # we were unable to make anything of the body, that's ok though because
                    # get a 200, so just save our error for posterity
                    result['message'] = "Event delivered successfully, ignoring response body, not JSON: %s" % unicode(e)

        except Exception as e:
            # we had an error, log it
            self.status = ERRORED
            result['message'] = "Error when delivering event - %s" % unicode(e)

        # if we had an error of some kind, schedule a retry for five minutes from now
        self.try_count += 1

        if self.status == ERRORED:
            if self.try_count < 3:
                self.next_attempt = timezone.now() + timedelta(minutes=5)
            else:
                self.next_attempt = None
                self.status = 'F'
        else:
            self.next_attempt = None

        return result

    def __unicode__(self):
        return "WebHookEvent[%s:%d] %s" % (self.event, self.pk, self.data)

class WebHookResult(SmartModel):
    """
    Represents the result of trying to deliver an event to a web hook
    """
    event = models.ForeignKey(WebHookEvent,
                              help_text="The event that this result is tied to")
    url = models.TextField(null=True, blank=True,
                           help_text="The URL the event was delivered to")
    data = models.TextField(null=True, blank=True,
                            help_text="The data that was posted to the webhook")
    request = models.TextField(null=True, blank=True,
                               help_text="The request that was posted to the webhook")
    status_code = models.IntegerField(help_text="The HTTP status as returned by the web hook")
    message = models.CharField(max_length=255,
                               help_text="A message describing the result, error messages go here")
    body = models.TextField(null=True, blank=True,
                            help_text="The body of the HTTP response as returned by the web hook")

    def stripped_body(self):
        return self.body.strip() if self.body else ""

    @classmethod
    def record_result(cls, event, result):
        # save our event
        event.save()

        # if our serializer was valid, save it, this will send the message out
        serializer = result.get('serializer', None)
        if serializer and serializer.is_valid():
            serializer.save()

        # little utility to trim a value by length
        message = result['message']
        if message:
            message = message[:255]

        api_user = get_api_user()

        WebHookResult.objects.create(event=event,
                                     url=result['url'],
                                     # Flow webhooks won't have 'request'
                                     request=result.get('request'),
                                     data=result['data'],
                                     message=message,
                                     status_code=result.get('status_code', 503),
                                     body=result.get('body', None),
                                     created_by=api_user,
                                     modified_by=api_user)

        # keep only the most recent 100 events for each org
        for old_event in WebHookEvent.objects.filter(org=event.org, status__in=['C', 'F']).order_by('-created_on')[100:]: # pragma: no cover
            old_event.delete()


class APIToken(models.Model):
    """
    Our API token, ties in orgs
    """
    key = models.CharField(max_length=40, primary_key=True)

    user = models.ForeignKey(User, related_name='api_tokens')

    org = models.ForeignKey(Org, related_name='api_tokens')

    created = models.DateTimeField(auto_now_add=True)

    role = models.ForeignKey(Group)

    @classmethod
    def get_orgs_for_role(cls, user, role):
        """
        Gets all the orgs the user can login to with the given role. Also
        takes a single character role (A, E, S, etc) and maps it to a UserGroup.
        """

        if role == 'A':
            valid_orgs = Org.objects.filter(administrators__in=[user])
            role = Group.objects.get(name='Administrators')
        elif role == 'E':
            # admins can authenticate as editors
            valid_orgs = Org.objects.filter(Q(administrators__in=[user]) | Q(editors__in=[user]))
            role = Group.objects.get(name='Editors')
        elif role == 'S':
            # admins and editors can authenticate as surveyors
            valid_orgs = Org.objects.filter(Q(administrators__in=[user]) | Q(editors__in=[user]) | Q(surveyors__in=[user]))
            role = Group.objects.get(name='Surveyors')
        else:
            # can't authenticate via the api as anything else
            valid_orgs = []
            role = None

        return valid_orgs, role

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = self.generate_key()
        return super(APIToken, self).save(*args, **kwargs)

    def generate_key(self):
        unique = uuid.uuid4()
        return hmac.new(unique.bytes, digestmod=sha1).hexdigest()

    def __unicode__(self):
        return self.key

    class Meta:
        unique_together = ('user', 'org', 'role')


def get_or_create_api_token(user):
    """
    Gets or (lazily creates) an API token for this user
    """
    if not user.is_authenticated():
        return None

    org = user.get_org()
    if not org:
        org = Org.get_org(user)

    role = user.get_role()

    if org:
        tokens = APIToken.objects.filter(user=user, org=org, role=role)

        if tokens:
            return str(tokens[0])
        else:
            token = APIToken.objects.create(user=user, org=org, role=role)
            return str(token)
    else:
        return None


def api_token(user):
    """
    Cached property access to a user's lazily-created API token
    """
    return get_cacheable_attr(user, '__api_token', lambda: get_or_create_api_token(user))


User.api_token = property(api_token)


def get_api_user():
    """
    Returns a user that can be used to associate events created by the API service
    """
    user = User.objects.filter(username='api')
    if user:
        return user[0]
    else:
        user = User.objects.create_user('api', 'code@temba.com')
        user.groups.add(Group.objects.get(name='Service Users'))
        return user
