# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations
from django.db.models import Prefetch, F
from temba.utils import chunk_list


FETCH_BATCH_SIZE = 1000000
UPDATE_BATCH_SIZE = 1000


def step_is_terminal(step, terminal_nodes):
    if step.step_uuid in terminal_nodes:
        return True  # an action set with no destination
    elif step.step_type == 'R' and step.left_on is None and step.rule_uuid is not None:
        return True  # a rule set that we never left even tho there was a matching rule
    else:
        return False


def populate_exit_type_migration(apps, schema_editor):
    """
    For running migration logic inside a database sync
    """
    FlowRun = apps.get_model('flows', 'FlowRun')
    FlowStep = apps.get_model('flows', 'FlowStep')
    ActionSet = apps.get_model('flows', 'ActionSet')

    populate_exit_type(FETCH_BATCH_SIZE, FlowRun, FlowStep, ActionSet)


def populate_exit_type_offline(batch_size=FETCH_BATCH_SIZE):
    """
    For running migration logic outside of an actual database sync
    """
    from temba.flows.models import FlowRun, FlowStep, ActionSet
    populate_exit_type(batch_size, FlowRun, FlowStep, ActionSet)


def populate_exit_type(batch_size, FlowRun, FlowStep, ActionSet):
    total = 0
    while True:
        # keep processing batches of runs until method returns 0
        updated = populate_exit_type_batch(batch_size, FlowRun, FlowStep, ActionSet)
        if updated:
            total += updated
            print "Updated total of %d flow runs so far" % total
        else:
            break


def populate_exit_type_batch(batch_size, FlowRun, FlowStep, ActionSet):
    # grab ids of a batch of inactive runs with no exit type
    exited_run_ids = FlowRun.objects.filter(is_active=False, exit_type=None)
    exited_run_ids = list(exited_run_ids.values_list('pk', flat=True)[:batch_size])

    if not exited_run_ids:
        return 0

    print "Fetched ids of %d potentially expired, completed or stopped runs" % len(exited_run_ids)

    # grab UUIDs of all terminal action sets for quick lookups
    terminal_nodes = set([n['uuid'] for n in ActionSet.objects.filter(destination=None).values('uuid')])
    if terminal_nodes:
        print "Cached %d terminal nodes for run completion calculation" % len(terminal_nodes)

    # pre-fetch required for completion calculation
    steps_prefetch = Prefetch('steps', queryset=FlowStep.objects.order_by('arrived_on'))

    num_updated = 0

    for batch_ids in chunk_list(exited_run_ids, UPDATE_BATCH_SIZE):
        completed_ids = []
        interrupted_ids = []
        expired_ids = []

        for run in FlowRun.objects.filter(pk__in=batch_ids).prefetch_related(steps_prefetch):
            # get last step in this run
            steps = list(run.steps.all())
            last_step = steps[len(steps) - 1] if len(steps) > 0 else None

            if last_step and step_is_terminal(last_step, terminal_nodes):
                completed_ids.append(run.pk)
            elif run.exited_on:
                expired_ids.append(run.pk)
            else:
                interrupted_ids.append(run.pk)

        # update our batches of completed/interrupted/expired, using modified_on as approximate exited_on
        if completed_ids:
            FlowRun.objects.filter(pk__in=completed_ids).update(exited_on=F('modified_on'), exit_type='C')
        if interrupted_ids:
            FlowRun.objects.filter(pk__in=interrupted_ids).update(exited_on=F('modified_on'), exit_type='I')
        if expired_ids:
            FlowRun.objects.filter(pk__in=expired_ids).update(exit_type='E')

        num_updated += len(completed_ids) + len(interrupted_ids) + len(expired_ids)

        print " > Updated %d of %d runs" % (num_updated, len(exited_run_ids))

    return len(exited_run_ids)


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0042_flowrun_exit_fields'),
    ]

    operations = [
        migrations.RunPython(populate_exit_type_migration)
    ]
