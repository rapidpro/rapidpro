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
        print("Starting population of Broadcast.base_language for %d total broadcasts..." % len(broadcast_ids))

    for id_batch in chunk_list(broadcast_ids, 1000):
        broadcast_steps = BroadcastSteps.objects.filter(broadcast_id__in=id_batch).distinct('broadcast_id')
        broadcast_steps = broadcast_steps.prefetch_related('flowstep__run__flow')

        # dict of language codes to lists of broadcast ids
        broadcasts_by_lang = defaultdict(list)

        for broadcast_step in broadcast_steps:
            flow = broadcast_step.flowstep.run.flow

            if flow.base_language:
                broadcasts_by_lang[flow.base_language].append(broadcast_step.broadcast_id)

        # update each set of broadcasts associated with a particular flow
        num_updated = 0
        for lang, bcast_ids in broadcasts_by_lang.items():
            Broadcast.objects.filter(id__in=bcast_ids).update(base_language=lang)
            num_updated += len(bcast_ids)

        num_processed += len(id_batch)
        print(" > Processed %d of %d broadcasts (updated %d with %d different languages)"
              % (num_processed, len(broadcast_ids), num_updated, len(broadcasts_by_lang)))

    if broadcast_ids:
        print("Finished population of Broadcast.base_language for %d total broadcasts" % len(broadcast_ids))


def apply_as_migration(apps, schema_editor):
    Broadcast = apps.get_model('msgs', 'Broadcast')
    FlowStep = apps.get_model('flows', 'FlowStep')

    do_populate(Broadcast, FlowStep)


def apply_manual():
    from temba.flows.models import FlowStep
    from temba.msgs.models import Broadcast

    do_populate(Broadcast, FlowStep)


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0068_broadcast_base_language'),
    ]

    operations = [
        migrations.RunPython(apply_as_migration)
    ]
