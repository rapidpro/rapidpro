# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations
from temba.utils import chunk_list


def populate_responded(apps, schema_editor):
    Msg = apps.get_model('msgs', 'Msg')
    FlowRun = apps.get_model('flows', 'FlowRun')

    # get all run ids with associated incoming flow messages
    run_responses = Msg.objects.filter(direction='I', msg_type='F').filter(steps__run__isnull=False)
    run_ids = list(run_responses.values_list('steps__run', flat=True).order_by('steps__run').distinct('steps__run'))

    num_total = len(run_ids)
    num_updated = 0

    # update in batches to avoid long-running table lock
    for batch_ids in chunk_list(run_ids, 1000):
        num_updated += FlowRun.objects.filter(pk__in=batch_ids).update(responded=True)

        print "Set responded flag on %d of %d runs" % (num_updated, num_total)


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0044_flowrun_responded'),
    ]

    operations = [
        migrations.RunPython(populate_responded)
    ]
