# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import hmac
import json
import requests
import six
import time
import uuid

from collections import OrderedDict
from datetime import timedelta
from django.db.models import Q
from django.conf import settings
from django.core.urlresolvers import reverse
from django.contrib.auth.models import User, Group
from django.db import models
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from hashlib import sha1
from rest_framework.permissions import BasePermission
from six.moves.urllib.parse import urlencode
from smartmin.models import SmartModel
from temba.channels.models import Channel, ChannelEvent
from temba.contacts.models import TEL_SCHEME
from temba.flows.models import FlowRun, ActionLog
from temba.orgs.models import Org
from temba.utils import prepped_request_to_str, on_transaction_commit
from temba.utils.cache import get_cacheable_attr
from temba.utils.http import http_headers
from temba.utils.models import JSONAsTextField


class APIPermission(BasePermission):
    """
    Verifies that the user has the permission set on the endpoint view
    """
    def has_permission(self, request, view):

        if getattr(view, 'permission', None):
            # no anon access to API endpoints
            if request.user.is_anonymous():
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
        if getattr(settings, 'SESSION_COOKIE_SECURE', False):
            return request.is_secure()
        else:
            return True


@six.python_2_unicode_compatible
class Resthook(SmartModel):
    """
    Represents a hook that a user creates on an organization. Outside apps can integrate by subscribing
    to this particular resthook.
    """
    org = models.ForeignKey(Org, related_name='resthooks',
                            help_text=_("The organization this resthook belongs to"))
    slug = models.SlugField(help_text=_("A simple label for this event"))

    @classmethod
    def get_or_create(cls, org, slug, user):
        """
        Looks up (or creates) the resthook for the passed in org and slug
        """
        slug = slug.lower()
        resthook = Resthook.objects.filter(is_active=True, org=org, slug=slug).first()
        if not resthook:
            resthook = Resthook.objects.create(org=org, slug=slug, created_by=user, modified_by=user)

        return resthook

    def get_subscriber_urls(self):
        return [s.target_url for s in self.subscribers.filter(is_active=True).order_by('created_on')]

    def add_subscriber(self, url, user):
        subscriber = self.subscribers.create(target_url=url, created_by=user, modified_by=user)
        self.modified_by = user
        self.save(update_fields=['modified_on', 'modified_by'])
        return subscriber

    def remove_subscriber(self, url, user):
        now = timezone.now()
        self.subscribers.filter(target_url=url, is_active=True).update(is_active=False, modified_on=now, modified_by=user)
        self.modified_by = user
        self.save(update_fields=['modified_on', 'modified_by'])

    def release(self, user):
        # release any active subscribers
        for s in self.subscribers.filter(is_active=True):
            s.release(user)

        # then ourselves
        self.is_active = False
        self.modified_by = user
        self.save(update_fields=['is_active', 'modified_on', 'modified_by'])

    def as_select2(self):
        return dict(text=self.slug, id=self.slug)

    def __str__(self):  # pragma: needs cover
        return six.text_type(self.slug)


class ResthookSubscriber(SmartModel):
    """
    Represents a subscriber on a specific resthook within one of our flows.
    """
    resthook = models.ForeignKey(Resthook, related_name='subscribers',
                                 help_text=_("The resthook being subscribed to"))
    target_url = models.URLField(help_text=_("The URL that we will call when our ruleset is reached"))

    def as_json(self):  # pragma: needs cover
        return dict(id=self.id, resthook=self.resthook.slug, target_url=self.target_url, created_on=self.created_on)

    def release(self, user):
        self.is_active = False
        self.modified_by = user
        self.save(update_fields=['is_active', 'modified_on', 'modified_by'])

        # update our parent as well
        self.resthook.modified_by = user
        self.resthook.save(update_fields=['modified_on', 'modified_by'])


class WebHookEvent(SmartModel):
    """
    Represents an event that needs to be sent to the web hook for a channel.
    """
    TYPE_SMS_RECEIVED = 'mo_sms'
    TYPE_SMS_SENT = 'mt_sent'
    TYPE_SMS_DELIVERED = 'mt_dlvd'
    TYPE_SMS_FAIL = 'mt_fail'
    TYPE_RELAYER_ALARM = 'alarm'
    TYPE_FLOW = 'flow'
    TYPE_CATEGORIZE = 'categorize'

    TYPE_CHOICES = ((TYPE_SMS_RECEIVED, "Incoming SMS Message"),
                    (TYPE_SMS_SENT, "Outgoing SMS Sent"),
                    (TYPE_SMS_DELIVERED, "Outgoing SMS Delivered to Recipient"),
                    (TYPE_SMS_FAIL, "Outgoing SMS Failed to be Delivered to Recipient"),
                    (ChannelEvent.TYPE_CALL_OUT, "Outgoing Call"),
                    (ChannelEvent.TYPE_CALL_OUT_MISSED, "Missed Outgoing Call"),
                    (ChannelEvent.TYPE_CALL_IN, "Incoming Call"),
                    (ChannelEvent.TYPE_CALL_IN_MISSED, "Missed Incoming Call"),
                    (TYPE_RELAYER_ALARM, "Channel Alarm"),
                    (TYPE_FLOW, "Flow Step Reached"),
                    (TYPE_CATEGORIZE, "Flow Categorization"))

    STATUS_PENDING = 'P'
    STATUS_COMPLETE = 'C'
    STATUS_FAILED = 'F'
    STATUS_ERRORED = 'E'

    STATUS_CHOICES = ((STATUS_PENDING, "Pending"),
                      (STATUS_COMPLETE, "Complete"),
                      (STATUS_ERRORED, "Errored"),
                      (STATUS_FAILED, "Failed"))

    org = models.ForeignKey(Org,
                            help_text="The organization that this event was triggered for")
    resthook = models.ForeignKey(Resthook, null=True,
                                 help_text="The associated resthook to this event. (optional)")
    status = models.CharField(max_length=1, choices=STATUS_CHOICES, default='P',
                              help_text="The state this event is currently in")
    run = models.ForeignKey(FlowRun, null=True,
                            help_text="The flow run that triggered this event")
    channel = models.ForeignKey(Channel, null=True, blank=True,
                                help_text="The channel that this event is relating to")
    event = models.CharField(max_length=16, choices=TYPE_CHOICES,
                             help_text="The event type for this event")
    data = JSONAsTextField(default=dict, help_text="The JSON encoded data that will be POSTED to the web hook")
    try_count = models.IntegerField(default=0,
                                    help_text="The number of times this event has been tried")
    next_attempt = models.DateTimeField(null=True, blank=True,
                                        help_text="When this event will be retried")
    action = models.CharField(max_length=8, default='POST', help_text='What type of HTTP event is it')

    @classmethod
    def get_recent_errored(cls, org):
        past_hour = timezone.now() - timedelta(hours=1)
        return cls.objects.filter(org=org, status__in=(cls.STATUS_FAILED, cls.STATUS_ERRORED), created_on__gte=past_hour)

    def fire(self):
        # start our task with this event id
        from .tasks import deliver_event_task
        on_transaction_commit(lambda: deliver_event_task.delay(self.id))

    @classmethod
    def trigger_flow_webhook(cls, run, webhook_url, node_uuid, msg, action='POST', resthook=None, headers=None):

        flow = run.flow
        contact = run.contact
        org = flow.org
        channel = msg.channel if msg else None
        contact_urn = msg.contact_urn if (msg and msg.contact_urn) else contact.get_urn()

        contact_dict = dict(uuid=contact.uuid, name=contact.name)
        if contact_urn:
            contact_dict['urn'] = contact_urn.urn

        post_data = {
            'contact': contact_dict,
            'flow': dict(name=flow.name, uuid=flow.uuid),
            'path': run.path,
            'results': run.results,
            'run': dict(uuid=six.text_type(run.uuid), created_on=run.created_on.isoformat())
        }

        if msg and msg.id > 0:
            post_data['input'] = dict(urn=msg.contact_urn.urn if msg.contact_urn else None, text=msg.text, attachments=(msg.attachments or []))

        if channel:
            post_data['channel'] = dict(name=channel.name, uuid=channel.uuid)

        api_user = get_api_user()
        if not action:  # pragma: needs cover
            action = 'POST'

        webhook_event = cls.objects.create(org=org, event=cls.TYPE_FLOW, channel=channel, data=post_data,
                                           run=run, try_count=1, action=action, resthook=resthook,
                                           created_by=api_user, modified_by=api_user)

        status_code = -1
        message = "None"
        body = None

        start = time.time()

        # webhook events fire immediately since we need the results back
        try:
            # no url, bail!
            if not webhook_url:
                raise ValueError("No webhook_url specified, skipping send")

            # only send webhooks when we are configured to, otherwise fail
            if settings.SEND_WEBHOOKS:
                requests_headers = http_headers(extra=headers)

                # some hosts deny generic user agents, use Temba as our user agent
                if action == 'GET':
                    response = requests.get(webhook_url, headers=requests_headers, timeout=10)
                else:
                    requests_headers['Content-type'] = 'application/json'
                    response = requests.post(webhook_url, data=json.dumps(post_data), headers=requests_headers, timeout=10)

                body = response.text
                if body:
                    body = body.strip()
                status_code = response.status_code
            else:
                print("!! Skipping WebHook send, SEND_WEBHOOKS set to False")
                body = 'Skipped actual send'
                status_code = 200

            # process the webhook response
            try:
                response_json = json.loads(body, object_pairs_hook=OrderedDict)

                # only update if we got a valid JSON dictionary or list
                if not isinstance(response_json, dict) and not isinstance(response_json, list):
                    raise ValueError("Response must be a JSON dictionary or list, ignoring response.")

                run.update_fields(response_json)
                message = "Webhook called successfully."
            except ValueError:
                message = "Response must be a JSON dictionary, ignoring response."

            if 200 <= status_code < 300:
                webhook_event.status = cls.STATUS_COMPLETE
            else:
                webhook_event.status = cls.STATUS_FAILED
                message = "Got non 200 response (%d) from webhook." % response.status_code
                raise Exception("Got non 200 response (%d) from webhook." % response.status_code)

        except Exception as e:
            import traceback
            traceback.print_exc()

            webhook_event.status = cls.STATUS_FAILED
            message = "Error calling webhook: %s" % six.text_type(e)

        finally:
            webhook_event.save(update_fields=('status',))

            # make sure our message isn't too long
            if message:
                message = message[:255]

            request_time = (time.time() - start) * 1000

            contact = None
            if webhook_event.run:
                contact = webhook_event.run.contact

            result = WebHookResult.objects.create(event=webhook_event,
                                                  contact=contact,
                                                  url=webhook_url,
                                                  status_code=status_code,
                                                  body=body,
                                                  message=message,
                                                  data=post_data,
                                                  request_time=request_time,
                                                  created_by=api_user,
                                                  modified_by=api_user)

            # if this is a test contact, add an entry to our action log
            if run.contact.is_test:
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
        if (event == cls.TYPE_SMS_RECEIVED and not org.is_notified_of_mo_sms()) or (event == cls.TYPE_SMS_SENT and not org.is_notified_of_mt_sms()) or (event == cls.TYPE_SMS_DELIVERED and not org.is_notified_of_mt_sms()):
            return

        api_user = get_api_user()

        json_time = time.strftime('%Y-%m-%dT%H:%M:%S.%f')
        data = dict(sms=msg.id,
                    phone=msg.contact.get_urn_display(org=org, scheme=TEL_SCHEME, formatted=False),
                    contact=msg.contact.uuid,
                    contact_name=msg.contact.name,
                    urn=six.text_type(msg.contact_urn),
                    text=msg.text,
                    attachments=[a.url for a in msg.get_attachments()],
                    time=json_time,
                    status=msg.status,
                    direction=msg.direction)

        hook_event = cls.objects.create(org=org, channel=msg.channel, event=event, data=data,
                                        created_by=api_user, modified_by=api_user)
        hook_event.fire()
        return hook_event

    @classmethod
    def trigger_call_event(cls, call):
        if not call.channel:  # pragma: needs cover
            return

        org = call.channel.org

        # no-op if no webhook configured
        if not org or not org.get_webhook_url():
            return

        event = call.event_type

        # if the org doesn't care about this type of message, ignore it
        if (event == ChannelEvent.TYPE_CALL_OUT and not org.is_notified_of_mt_call()) or \
           (event == ChannelEvent.TYPE_CALL_OUT_MISSED and not org.is_notified_of_mt_call()) or \
           (event == ChannelEvent.TYPE_CALL_IN and not org.is_notified_of_mo_call()) or \
           (event == ChannelEvent.TYPE_CALL_IN_MISSED and not org.is_notified_of_mo_call()):
            return

        api_user = get_api_user()

        json_time = call.occurred_on.strftime('%Y-%m-%dT%H:%M:%S.%f')
        data = dict(call=call.pk,
                    phone=call.contact.get_urn_display(org=org, scheme=TEL_SCHEME, formatted=False),
                    contact=call.contact.uuid,
                    contact_name=call.contact.name,
                    urn=six.text_type(call.contact_urn),
                    extra=call.extra,
                    occurred_on=json_time)
        hook_event = cls.objects.create(org=org, channel=call.channel, event=event, data=data,
                                        created_by=api_user, modified_by=api_user)
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

        api_user = get_api_user()

        json_time = channel.last_seen.strftime('%Y-%m-%dT%H:%M:%S.%f')
        data = dict(channel=channel.pk,
                    channel_uuid=channel.uuid,
                    power_source=sync_event.power_source,
                    power_status=sync_event.power_status,
                    power_level=sync_event.power_level,
                    network_type=sync_event.network_type,
                    pending_message_count=sync_event.pending_message_count,
                    retry_message_count=sync_event.retry_message_count,
                    last_seen=json_time)

        hook_event = cls.objects.create(org=org, channel=channel, event=cls.TYPE_RELAYER_ALARM, data=data,
                                        created_by=api_user, modified_by=api_user)
        hook_event.fire()
        return hook_event

    def deliver(self):
        from .v1.serializers import MsgCreateSerializer
        start = time.time()

        # create our post parameters
        post_data = self.data
        post_data['event'] = self.event
        post_data['relayer'] = self.channel.pk if self.channel else ''
        post_data['channel'] = self.channel.pk if self.channel else ''
        post_data['relayer_phone'] = self.channel.address if self.channel else ''

        # look up the endpoint for this channel
        result = dict(url=self.org.get_webhook_url(), data=urlencode(post_data, doseq=True))

        if not self.org.get_webhook_url():  # pragma: no cover
            result['status_code'] = 0
            result['message'] = "No webhook registered for this org, ignoring event"
            self.status = self.STATUS_FAILED
            self.next_attempt = None
            return result

        # get our org user
        user = self.org.get_user()

        # no user?  we shouldn't be doing webhooks shtuff
        if not user:
            result['status_code'] = 0
            result['message'] = "No active user for this org, ignoring event"
            self.status = self.STATUS_FAILED
            self.next_attempt = None
            return result

        # make the request
        try:
            if not settings.SEND_WEBHOOKS:
                raise Exception("!! Skipping WebHook send, SEND_WEBHOOKS set to False")

            headers = http_headers(extra=self.org.get_webhook_headers())

            s = requests.Session()
            prepped = requests.Request('POST', self.org.get_webhook_url(),
                                       data=post_data,
                                       headers=headers).prepare()
            result['url'] = prepped.url
            result['request'] = prepped_request_to_str(prepped)
            r = s.send(prepped, timeout=5)

            result['status_code'] = r.status_code
            result['body'] = r.text.strip()

            r.raise_for_status()

            # any 200 code is ok by us
            self.status = self.STATUS_COMPLETE
            result['request_time'] = (time.time() - start) * 1000
            result['message'] = "Event delivered successfully."

            # read our body if we have one
            if result['body']:
                try:
                    data = r.json()
                    serializer = MsgCreateSerializer(data=data, user=user, org=self.org)

                    if serializer.is_valid():
                        result['serializer'] = serializer
                        result['message'] = "Response body contains message which will be sent"
                    else:
                        errors = serializer.errors
                        result['message'] = "Event delivered successfully, ignoring response body, wrong format: %s" % \
                                            ",".join("%s: %s" % (_, ",".join(errors[_])) for _ in errors.keys())

                except ValueError as e:
                    # we were unable to make anything of the body, that's ok though because
                    # get a 200, so just save our error for posterity
                    result['message'] = "Event delivered successfully, ignoring response body, not JSON: %s" % six.text_type(e)

        except Exception as e:
            # we had an error, log it
            self.status = self.STATUS_ERRORED
            result['request_time'] = time.time() - start
            result['message'] = "Error when delivering event - %s" % six.text_type(e)

        # if we had an error of some kind, schedule a retry for five minutes from now
        self.try_count += 1

        if self.status == self.STATUS_ERRORED:
            if self.try_count < 3:
                self.next_attempt = timezone.now() + timedelta(minutes=5)
            else:
                self.next_attempt = None
                self.status = 'F'
        else:
            self.next_attempt = None

        return result

    def __str__(self):  # pragma: needs cover
        return "WebHookEvent[%s:%d] %s" % (self.event, self.pk, self.data)


class WebHookResult(SmartModel):
    """
    Represents the result of trying to deliver an event to a web hook
    """
    event = models.ForeignKey(WebHookEvent, related_name='results',
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
    request_time = models.IntegerField(null=True, help_text=_('Time it took to process this request'))
    contact = models.ForeignKey('contacts.Contact', null=True, related_name='webhook_results',
                                help_text="The contact that generated this result")

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

        request_time = result.get('request_time', None)

        cls.objects.create(event=event,
                           url=result['url'],
                           request=result.get('request'),  # flow webhooks won't have 'request'
                           data=result['data'],
                           message=message,
                           status_code=result.get('status_code', 503),
                           body=result.get('body', None),
                           request_time=request_time,
                           created_by=api_user,
                           modified_by=api_user)

    @property
    def is_success(self):
        return 200 <= self.status_code < 300


@six.python_2_unicode_compatible
class APIToken(models.Model):
    """
    Our API token, ties in orgs
    """
    CODE_TO_ROLE = {'A': "Administrators", 'E': "Editors", 'S': "Surveyors"}

    ROLE_GRANTED_TO = {"Administrators": ("Administrators",),
                       "Editors": ("Administrators", "Editors"),
                       "Surveyors": ("Administrators", "Editors", "Surveyors")}

    is_active = models.BooleanField(default=True)

    key = models.CharField(max_length=40, primary_key=True)

    user = models.ForeignKey(User, related_name='api_tokens')

    org = models.ForeignKey(Org, related_name='api_tokens')

    created = models.DateTimeField(auto_now_add=True)

    role = models.ForeignKey(Group)

    @classmethod
    def get_or_create(cls, org, user, role=None, refresh=False):
        """
        Gets or creates an API token for this user
        """
        if not role:
            role = cls.get_default_role(org, user)

        if not role:
            raise ValueError("User '%s' has no suitable role for API usage" % six.text_type(user))
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
            for role_name, granted_to in six.iteritems(cls.ROLE_GRANTED_TO):
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
        return super(APIToken, self).save(*args, **kwargs)

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
