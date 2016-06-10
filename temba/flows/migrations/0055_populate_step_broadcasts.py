# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from temba.utils import chunk_list
from redis_cache import get_redis_connection
import time

HIGHPOINT_KEY = 'flowstep_backfill_highpoint'


def populate_flowsteps_for_broadcast(RelatedBroadcast, RelatedMsg, MsgManager, broadcast, batch):
    msg_ids = MsgManager.filter(broadcast=broadcast.id).values_list('id', flat=True)
    start_count = len(batch)

    for msg_id_batch in chunk_list(set(msg_ids), 1000):
        fs_ids = set(RelatedMsg.objects.filter(msg_id__in=msg_id_batch).values_list('flowstep_id', flat=True))
        broadcast_batch = [RelatedBroadcast(flowstep_id=fs_id, broadcast_id=broadcast.id) for fs_id in fs_ids]
        batch += broadcast_batch

    return len(batch) - start_count


def backfill_flowsteps(FlowStep, Broadcast, MsgManager):
    # we keep track of our completed broadcasts so we can pick up where we left off if interrupted
    r = get_redis_connection()
    highpoint = r.get(HIGHPOINT_KEY)
    if highpoint is None:
        highpoint = 0

    RelatedBroadcast = FlowStep.broadcasts.through
    RelatedMsg = FlowStep.messages.through

    broadcast_ids = Broadcast.objects.filter(id__gt=highpoint).order_by('id').values_list('id', flat=True)
    start = time.time()
    batch = []
    i = 0

    for broadcast_id_batch in chunk_list(broadcast_ids, 1000):
        broadcasts = Broadcast.objects.filter(id__in=broadcast_id_batch).order_by('id').only('id')
        for broadcast in broadcasts:
            i += 1

            # clear any current relations on this broadcast
            RelatedBroadcast.objects.filter(broadcast_id=broadcast.id).delete()

            populate_flowsteps_for_broadcast(RelatedBroadcast, RelatedMsg, MsgManager, broadcast, batch)
            if len(batch) > 1000:
                for broadcast_batch in chunk_list(batch, 1000):
                    RelatedBroadcast.objects.bulk_create(broadcast_batch)
                r.set(HIGHPOINT_KEY, broadcast.id)
                batch = []

        print "Processed %d / %d (batch size %d) in %d" % (i, len(broadcast_ids), len(batch), int(time.time() - start))

    for broadcast_batch in chunk_list(batch, 1000):
        RelatedBroadcast.objects.bulk_create(broadcast_batch)

    # we finished, no need to track any more status
    r.delete(HIGHPOINT_KEY)


def migration_backfill_flowsteps(apps, schema):
    backfill_flowsteps(apps.get_model('flows', 'FlowStep'),
                        apps.get_model('msgs', 'Broadcast'),
                        apps.get_model('msgs', 'Msg').objects)


def noop(apps, schema):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0054_flowstep_broadcasts'),
    ]

    operations = [
        migrations.RunPython(migration_backfill_flowsteps, noop)
    ]
