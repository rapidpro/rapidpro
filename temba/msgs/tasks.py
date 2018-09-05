
import logging
import time
from datetime import timedelta

import iso8601
from django_redis import get_redis_connection

from django.core.cache import cache
from django.utils import timezone
from django.utils.encoding import force_text

from celery.task import task

from temba.channels.courier import handle_new_contact, handle_new_message
from temba.channels.models import CHANNEL_EVENT, ChannelEvent
from temba.contacts.models import STOP_CONTACT_EVENT, Contact
from temba.utils import analytics, chunk_list, json
from temba.utils.queues import complete_task, nonoverlapping_task, start_task

from .models import (
    FIRE_EVENT,
    HANDLE_EVENT_TASK,
    MSG_EVENT,
    PENDING,
    TIMEOUT_EVENT,
    Broadcast,
    BroadcastMsgCount,
    ExportMessagesTask,
    LabelCount,
    Msg,
    SystemLabelCount,
)

logger = logging.getLogger(__name__)


def process_run_timeout(run_id, timeout_on):
    """
    Processes a single run timeout
    """
    from temba.flows.models import FlowRun

    r = get_redis_connection()
    run = FlowRun.objects.filter(id=run_id, is_active=True, flow__is_active=True).first()

    if run:
        key = "pcm_%d" % run.contact_id
        if not r.get(key):
            with r.lock(key, timeout=120):
                print("T[%09d] Processing timeout" % run.id)
                start = time.time()

                run.refresh_from_db()

                # this is still the timeout to process (json doesn't have microseconds so close enough)
                if run.timeout_on and abs(run.timeout_on - timeout_on) < timedelta(milliseconds=1):
                    run.resume_after_timeout(timeout_on)
                else:
                    print("T[%09d] .. skipping timeout, already handled" % run.id)

                print("T[%09d] %08.3f s" % (run.id, time.time() - start))


def process_fire_events(fire_ids):
    from temba.campaigns.models import EventFire

    # every event fire in the batch will be for the same flow... but if the flow has been deleted then fires won't exist
    single_fire = EventFire.objects.filter(id__in=fire_ids).first()
    if not single_fire:  # pragma: no cover
        return

    flow = single_fire.event.flow

    # lock on the flow so we know non-one else is updating these event fires
    r = get_redis_connection()
    with r.lock("process_fire_events:%d" % flow.id, timeout=300):

        # only fetch fires that haven't been somehow already handled
        fires = list(EventFire.objects.filter(id__in=fire_ids, fired=None).prefetch_related("contact"))
        if fires:
            print("E[%s][%s] Batch firing %d events..." % (flow.org.name, flow.name, len(fires)))

            start = time.time()
            EventFire.batch_fire(fires, flow)

            print("E[%s][%s] Finished batch firing events in %.3f s" % (flow.org.name, flow.name, time.time() - start))


def process_message(msg, new_message=False, new_contact=False):
    """
    Processes the passed in message dealing with new contacts or mage messages appropriately.
    """
    print("M[%09d] Processing - %s" % (msg.id, msg.text))
    start = time.time()

    # if message was created in Mage...
    if new_message:
        handle_new_message(msg.org, msg)
        if new_contact:
            handle_new_contact(msg.org, msg.contact)

    Msg.process_message(msg)
    print("M[%09d] %08.3f s - %s" % (msg.id, time.time() - start, msg.text))


@task(track_started=True, name="process_message_task")
def process_message_task(msg_event):
    """
    Given the task JSON from our queue, processes the message, is two implementations to deal with
    backwards compatibility of using contact queues (second branch can be removed later)
    """
    r = get_redis_connection()

    # we have a contact id, we want to get the msg from that queue after acquiring our lock
    if msg_event.get("contact_id"):
        key = "pcm_%d" % msg_event["contact_id"]
        contact_queue = Msg.CONTACT_HANDLING_QUEUE % msg_event["contact_id"]

        # wait for the lock as we want to make sure to process the next message as soon as we are free
        with r.lock(key, timeout=120):

            # pop the next message off our contact queue until we find one that needs handling
            while True:
                with r.pipeline() as pipe:
                    pipe.zrange(contact_queue, 0, 0)
                    pipe.zremrangebyrank(contact_queue, 0, 0)
                    (contact_msg, deleted) = pipe.execute()

                # no more messages in the queue for this contact, we're done
                if not contact_msg:
                    return

                # we have a message in our contact queue, look it up
                msg_event = json.loads(force_text(contact_msg[0]))
                msg = (
                    Msg.objects.filter(id=msg_event["id"])
                    .order_by()
                    .select_related("org", "contact", "contact_urn", "channel")
                    .first()
                )

                # make sure we are still pending
                if msg and msg.status == PENDING:
                    process_message(msg, msg_event.get("new_message", False), msg_event.get("new_contact", False))
                    return

    # backwards compatibility for events without contact ids, we handle the message directly
    else:
        msg = Msg.objects.filter(id=msg_event["id"]).select_related("org", "contact", "contact_urn", "channel").first()
        if msg and msg.status == PENDING:
            # grab our contact lock and handle this message
            key = "pcm_%d" % msg.contact_id
            with r.lock(key, timeout=120):
                process_message(msg, msg_event.get("new_message", False), msg_event.get("new_contact", False))


@task(track_started=True, name="send_broadcast")
def send_broadcast_task(broadcast_id, **kwargs):
    # get our broadcast
    from .models import Broadcast

    broadcast = Broadcast.objects.get(pk=broadcast_id)

    high_priority = broadcast.recipient_count == 1
    expressions_context = {} if kwargs.get("with_expressions", True) else None

    broadcast.send(high_priority=high_priority, expressions_context=expressions_context)


@task(track_started=True, name="send_to_flow_node")
def send_to_flow_node(org_id, user_id, text, **kwargs):
    from django.contrib.auth.models import User
    from temba.contacts.models import Contact
    from temba.orgs.models import Org
    from temba.flows.models import FlowRun

    org = Org.objects.get(pk=org_id)
    user = User.objects.get(pk=user_id)
    simulation = kwargs.get("simulation", "false") == "true"
    node_uuid = kwargs.get("s", None)

    runs = FlowRun.objects.filter(org=org, current_node_uuid=node_uuid, is_active=True)

    contact_ids = (
        Contact.objects.filter(org=org, is_blocked=False, is_stopped=False, is_active=True, is_test=simulation)
        .filter(id__in=runs.values_list("contact", flat=True))
        .values_list("id", flat=True)
    )

    broadcast = Broadcast.create(org, user, text, contact_ids=contact_ids)
    broadcast.send(expressions_context={})

    analytics.track(user.username, "temba.broadcast_created", dict(contacts=len(contact_ids), groups=0, urns=0))


@task(track_started=True, name="send_spam")
def send_spam(user_id, contact_id):  # pragma: no cover
    """
    Processses a single incoming message through our queue.
    """
    from django.contrib.auth.models import User
    from temba.contacts.models import Contact, TEL_SCHEME
    from temba.msgs.models import Broadcast

    contact = Contact.all().get(pk=contact_id)
    user = User.objects.get(pk=user_id)
    channel = contact.org.get_send_channel(TEL_SCHEME)

    if not channel:  # pragma: no cover
        print("Sorry, no channel to be all spammy with")
        return

    long_text = "Test Message #%d. The path of the righteous man is beset on all sides by the iniquities of the " "selfish and the tyranny of evil men. Blessed is your face."

    # only trigger sync on the last one
    for idx in range(10):
        broadcast = Broadcast.create(contact.org, user, long_text % (idx + 1), contacts=[contact])
        broadcast.send(trigger_send=(idx == 149))


@task(track_started=True, name="fail_old_messages")
def fail_old_messages():  # pragma: needs cover
    Msg.fail_old_messages()


@nonoverlapping_task(track_started=True, name="collect_message_metrics_task", time_limit=900)
def collect_message_metrics_task():  # pragma: needs cover
    """
    Collects message metrics and sends them to our analytics.
    """
    from .models import INCOMING, OUTGOING, PENDING, QUEUED, ERRORED, INITIALIZING
    from temba.utils import analytics

    # current # of queued messages (excluding Android)
    count = (
        Msg.objects.filter(direction=OUTGOING, status=QUEUED)
        .exclude(channel=None)
        .exclude(topup=None)
        .exclude(channel__channel_type="A")
        .exclude(next_attempt__gte=timezone.now())
        .count()
    )
    analytics.gauge("temba.current_outgoing_queued", count)

    # current # of initializing messages (excluding Android)
    count = (
        Msg.objects.filter(direction=OUTGOING, status=INITIALIZING)
        .exclude(channel=None)
        .exclude(topup=None)
        .exclude(channel__channel_type="A")
        .count()
    )
    analytics.gauge("temba.current_outgoing_initializing", count)

    # current # of pending messages (excluding Android)
    count = (
        Msg.objects.filter(direction=OUTGOING, status=PENDING)
        .exclude(channel=None)
        .exclude(topup=None)
        .exclude(channel__channel_type="A")
        .count()
    )
    analytics.gauge("temba.current_outgoing_pending", count)

    # current # of errored messages (excluding Android)
    count = (
        Msg.objects.filter(direction=OUTGOING, status=ERRORED)
        .exclude(channel=None)
        .exclude(topup=None)
        .exclude(channel__channel_type="A")
        .count()
    )
    analytics.gauge("temba.current_outgoing_errored", count)

    # current # of android outgoing messages waiting to be sent
    count = (
        Msg.objects.filter(direction=OUTGOING, status__in=[PENDING, QUEUED], channel__channel_type="A")
        .exclude(channel=None)
        .exclude(topup=None)
        .count()
    )
    analytics.gauge("temba.current_outgoing_android", count)

    # current # of pending incoming messages that haven't yet been handled
    count = Msg.objects.filter(direction=INCOMING, status=PENDING).exclude(channel=None).count()
    analytics.gauge("temba.current_incoming_pending", count)

    # stuff into redis when we last run, we do this as a canary as to whether our tasks are falling behind or not running
    cache.set("last_cron", timezone.now())


@nonoverlapping_task(track_started=True, name="check_messages_task", time_limit=900)
def check_messages_task():  # pragma: needs cover
    """
    Checks to see if any of our aggregators have errored messages that need to be retried.
    Also takes care of flipping Contacts from Failed to Normal and back based on their status.
    """
    from .models import INCOMING, PENDING
    from temba.orgs.models import Org
    from temba.channels.tasks import send_msg_task
    from temba.flows.tasks import start_msg_flow_batch_task

    now = timezone.now()
    five_minutes_ago = now - timedelta(minutes=5)
    r = get_redis_connection()

    # for any org that sent messages in the past five minutes, check for pending messages
    for org in Org.objects.filter(msgs__created_on__gte=five_minutes_ago).distinct():
        # more than 1,000 messages queued? don't do anything, wait for our queue to go down
        queued = r.zcard("send_message_task:%d" % org.id)
        if queued < 1000:
            org.trigger_send()

    # fire a few send msg tasks in case we dropped one somewhere during a restart
    # (these will be no-ops if there is nothing to do)
    for i in range(100):
        send_msg_task.apply_async(queue="msgs")
        handle_event_task.apply_async(queue="handler")
        start_msg_flow_batch_task.apply_async(queue="flows")

    # also check any incoming messages that are still pending somehow, reschedule them to be handled
    unhandled_messages = Msg.objects.filter(direction=INCOMING, status=PENDING, created_on__lte=five_minutes_ago)
    unhandled_messages = unhandled_messages.exclude(channel__is_active=False).exclude(contact__is_test=True)
    unhandled_count = unhandled_messages.count()

    if unhandled_count:
        print("** Found %d unhandled messages" % unhandled_count)
        for msg in unhandled_messages[:100]:
            msg.handle()


@task(track_started=True, name="export_sms_task")
def export_messages_task(export_id):
    """
    Export messages to a file and e-mail a link to the user
    """
    ExportMessagesTask.objects.get(id=export_id).perform()


@task(track_started=True, name="handle_event_task", time_limit=180, soft_time_limit=120)
def handle_event_task():
    """
    Priority queue task that handles both event fires (when fired) and new incoming
    messages that need to be handled.

    Currently three types of events may be "popped" from our queue:
             msg - Which contains the id of the Msg to be processed
            fire - Which contains the id of the EventFire that needs to be fired
         timeout - Which contains a run that timed out and needs to be resumed
    stop_contact - Which contains the contact id to stop
    """
    # pop off the next task
    org_id, event_task = start_task(HANDLE_EVENT_TASK)

    # it is possible we have no message to send, if so, just return
    if not event_task:  # pragma: needs cover
        return

    try:
        if event_task["type"] == MSG_EVENT:
            process_message_task(event_task)

        elif event_task["type"] == FIRE_EVENT:
            fire_ids = event_task.get("fires") if "fires" in event_task else [event_task.get("id")]
            process_fire_events(fire_ids)

        elif event_task["type"] == TIMEOUT_EVENT:
            timeout_on = iso8601.parse_date(event_task["timeout_on"])
            process_run_timeout(event_task["run"], timeout_on)

        elif event_task["type"] == STOP_CONTACT_EVENT:
            contact = Contact.objects.get(id=event_task["contact_id"])
            contact.stop(contact.modified_by)

        elif event_task["type"] == CHANNEL_EVENT:
            event = ChannelEvent.objects.get(id=event_task["event_id"])
            event.handle()

        else:  # pragma: needs cover
            raise Exception("Unexpected event type: %s" % event_task)
    finally:
        complete_task(HANDLE_EVENT_TASK, org_id)


@nonoverlapping_task(track_started=True, name="squash_msgcounts")
def squash_msgcounts():
    SystemLabelCount.squash()
    LabelCount.squash()
    BroadcastMsgCount.squash()


@nonoverlapping_task(track_started=True, name="clear_old_msg_external_ids", time_limit=60 * 60 * 36)
def clear_old_msg_external_ids():
    """
    Clears external_id on older messages to reduce the size of the index on that column. External ids aren't surfaced
    anywhere and are only used for debugging channel issues, so are of limited usefulness on older messages.
    """
    threshold = timezone.now() - timedelta(days=30)  # 30 days ago

    msg_ids = list(Msg.objects.filter(created_on__lt=threshold).exclude(external_id=None).values_list("id", flat=True))

    for msg_id_batch in chunk_list(msg_ids, 1000):
        Msg.objects.filter(id__in=msg_id_batch).update(external_id=None)

    print("Cleared external ids on %d messages" % len(msg_ids))
