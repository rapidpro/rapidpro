# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import time
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

    Msg.objects = Msg.all_messages

    populate_responded(batch_size, Msg, FlowRun)


def populate_responded(batch_size, Msg, FlowRun):
    total_responded, total_unresponded = 0, 0

    start = time.time()

    # first update all runs where responded is true
    while True:
        # keep processing batches of runs until method returns 0
        batch_start = time.time()
        num_responded, num_unresponded = populate_responded_batch(batch_size, Msg, FlowRun)
        taken = int((time.time() - batch_start) / 60)
        if num_responded or num_unresponded:
            total_responded += num_responded
            total_unresponded += num_unresponded
            total = total_responded + total_unresponded
            print "Updated %d flow runs in %d mins (%d total, %d with responses, %d without)" \
                  % (num_responded + num_unresponded, taken, total, total_responded, total_unresponded)
        else:
            break

    if total_responded or total_unresponded:
        print "Total running time: %d mins" % int((time.time() - start) / 60)


def populate_responded_batch(batch_size, Msg, FlowRun):
    # grab ids of a batch of runs with null responded
    run_ids = FlowRun.objects.filter(responded=None)
    run_ids = list(run_ids.values_list('pk', flat=True)[:batch_size])

    if not run_ids:
        return 0, 0

    print "Fetched ids of %d runs with no responded value..." % len(run_ids)

    total_with, total_without = 0, 0

    for batch_ids in chunk_list(run_ids, UPDATE_BATCH_SIZE):
        batch_ids = list(batch_ids)

        # which of the runs in this batch have responses?
        msg_responses = Msg.objects.filter(direction='I', steps__run__pk__in=batch_ids)
        with_responses = msg_responses.values_list('steps__run', flat=True)

        with_responses = set(with_responses)
        without_responses = [run_id for run_id in batch_ids if run_id not in with_responses]

        # update our batches of responded/un-responded
        if with_responses:
            FlowRun.objects.filter(pk__in=with_responses).update(responded=True)
        if without_responses:
            FlowRun.objects.filter(pk__in=without_responses).update(responded=False)

        total_with += len(with_responses)
        total_without += len(without_responses)

        print " > Updated %d of %d runs batch" % (total_with + total_without, len(run_ids))

    return total_with, total_without


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0044_flowrun_responded'),
    ]

    operations = [
        migrations.RunPython(populate_responded_migration)
    ]
