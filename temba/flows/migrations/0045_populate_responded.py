# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations
from temba.utils import chunk_list


FETCH_BATCH_SIZE = 1000000
UPDATE_BATCH_SIZE = 1000


def populate_responded_migration(apps, schema_editor):
    """
    For running migration logic inside a database sync
    """
    Msg = apps.get_model('msgs', 'Msg')
    FlowRun = apps.get_model('flows', 'FlowRun')

    populate_responded(FETCH_BATCH_SIZE, Msg, FlowRun)


def populate_responded_offline(batch_size=FETCH_BATCH_SIZE):
    """
    For running migration logic outside of an actual database sync
    """
    from temba.msgs.models import Msg
    from temba.flows.models import FlowRun

    populate_responded(batch_size, Msg, FlowRun)


def populate_responded(batch_size, Msg, FlowRun):
    total = 0
    while True:
        # keep processing batches of runs until method returns 0
        updated = populate_responded_batch(batch_size, Msg, FlowRun)
        if updated:
            total += updated
            print "Updated total of %d flow runs so far" % total
        else:
            break


def populate_responded_batch(batch_size, Msg, FlowRun):
    # get next batch of run ids with associated incoming flow messages where responded is not set
    run_responses = Msg.objects.filter(direction='I', msg_type='F')
    run_responses = run_responses.filter(steps__run__isnull=False, steps__run__responded=False)
    run_ids = run_responses.values_list('steps__run', flat=True).order_by('steps__run').distinct('steps__run')
    run_ids = list(run_ids[:batch_size])

    num_total = len(run_ids)
    if not num_total:
        return 0

    print "Fetched %d run ids to update..." % num_total

    num_updated = 0

    # update in batches to avoid long-running table lock
    for batch_ids in chunk_list(run_ids, 1000):
        num_updated += FlowRun.objects.filter(pk__in=batch_ids).update(responded=True)

        print "Set responded flag on %d of %d runs" % (num_updated, num_total)

    return num_total


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0044_flowrun_responded'),
    ]

    operations = [
        migrations.RunPython(populate_responded_migration)
    ]
