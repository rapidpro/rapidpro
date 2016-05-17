# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from temba.utils import chunk_list
from redis_cache import get_redis_connection
import time

HIGHPOINT_KEY = 'recipient_backfill_highpoint'

def populate_recipients_for_broadcast(Broadcast, MsgManager, broadcast_id):
    """
    Populates the recipients for the passed in broadcast, we just select all the
    msgs for this broadcast, then populate the recipients based on the URNs of
    those messages
    """
    urn_ids = MsgManager.filter(broadcast=broadcast_id).values_list('contact_urn_id', flat=True)

    # clear any current recipients, we are rebuilding
    RelatedRecipients = Broadcast.recipients.through
    Broadcast.objects.get(id=broadcast_id).recipients.clear()

    for urn_batch in chunk_list(set(urn_ids), 1000):
        recipient_batch = [RelatedRecipients(contacturn_id=u, broadcast_id=broadcast_id) for u in urn_batch]
        RelatedRecipients.objects.bulk_create(recipient_batch)

    return len(urn_ids)


def backfill_recipients(Broadcast, MsgManager):
    # we keep track of our completed broadcasts so we can pick up where we left off if interrupted
    r = get_redis_connection()
    highpoint = r.get(HIGHPOINT_KEY)
    if highpoint is None:
        highpoint = 0

    broadcast_ids = Broadcast.objects.filter(id__gt=highpoint).order_by('id').values_list('id', flat=True)
    start = time.time()
    for (i, broadcast_id) in enumerate(broadcast_ids):
        recipient_count = populate_recipients_for_broadcast(Broadcast, MsgManager, broadcast_id)
        print "%d - %d ... (%d of %d) in %d" % (broadcast_id, recipient_count, i, len(broadcast_ids)-1, int(time.time() - start))
        r.set(HIGHPOINT_KEY, broadcast_id)

    # we finished, no need to track any more status
    r.delete(HIGHPOINT_KEY)


def migration_backfill_recipients(apps, schema):
    backfill_recipients(apps.get_model('msgs', 'Broadcast'), apps.get_model('msgs', 'Msg').objects)

def noop(apps, schema):
    pass

class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0054_auto_20160426_2130'),
    ]

    operations = [
        migrations.RunPython(migration_backfill_recipients, noop)
    ]
