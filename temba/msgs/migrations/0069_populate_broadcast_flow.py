# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from collections import defaultdict
from django.db import migrations
from temba.utils import chunk_list


def do_populate(Broadcast, FlowStep):
    BroadcastSteps = FlowStep.broadcasts.through
    broadcast_ids = list(Broadcast.objects.values_list('id', flat=True).order_by('org_id'))
    num_processed = 0

    if broadcast_ids:
        print("Starting population of Broadcast.flow for %d total broadcasts..." % len(broadcast_ids))

    for id_batch in chunk_list(broadcast_ids, 1000):
        broadcast_steps = BroadcastSteps.objects.filter(broadcast_id__in=id_batch).distinct('broadcast_id')
        broadcast_steps = broadcast_steps.prefetch_related('flowstep__run')

        # dict of flow ids to lists of broadcast ids
        broadcasts_by_flow = defaultdict(list)

        for broadcast_step in broadcast_steps:
            flow_id = broadcast_step.flowstep.run.flow_id
            broadcasts_by_flow[flow_id].append(broadcast_step.broadcast_id)

        # update each set of broadcasts associated with a particular flow
        num_updated = 0
        for flow_id, bcast_ids in broadcasts_by_flow.items():
            Broadcast.objects.filter(id__in=bcast_ids).update(flow_id=flow_id)
            num_updated += len(bcast_ids)

        num_processed += len(id_batch)
        print(" > Processed %d of %d broadcasts (updated %d in %d flows)"
              % (num_processed, len(broadcast_ids), num_updated, len(broadcasts_by_flow)))

    if broadcast_ids:
        print("Finished population of Broadcast.flow for %d total broadcasts" % len(broadcast_ids))


def run_as_migration(apps, schema_editor):
    Broadcast = apps.get_model('msgs', 'Broadcast')
    FlowStep = apps.get_model('flows', 'FlowStep')

    do_populate(Broadcast, FlowStep)


def run_offline():
    from temba.flows.models import FlowStep
    from temba.msgs.models import Broadcast

    do_populate(Broadcast, FlowStep)


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0068_broadcast_flow'),
    ]

    operations = [
        migrations.RunPython(run_as_migration)
    ]
