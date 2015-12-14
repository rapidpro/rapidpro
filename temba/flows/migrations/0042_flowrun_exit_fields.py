# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.db.models import Prefetch
from temba.utils.models import ChunkIterator


def step_is_terminal(step, terminal_nodes):
    if step.step_uuid in terminal_nodes:
        return True  # an action set with no destination
    elif step.step_type == 'R' and step.left_on is None and step.rule_uuid is not None:
        return True  # a rule set that we never left even tho there was a matching rule
    else:
        return False


def populate_exit_type(apps, schema_editor):
    print "REMOVE (rename complete)"

    FlowRun = apps.get_model('flows', 'FlowRun')
    FlowStep = apps.get_model('flows', 'FlowStep')
    ActionSet = apps.get_model('flows', 'ActionSet')

    num_completed = 0
    num_restarted = 0

    # only expired runs at this point have non-null exited_on (as it's expired_on renamed)
    num_expired = FlowRun.objects.exclude(exited_on=None).update(exit_type='E')
    if num_expired:
        print "Set exit type for %d expired runs" % num_expired

    # grab ids of remaining inactive runs which may have been completed or restarted
    exited_run_ids = [r['pk'] for r in FlowRun.objects.filter(is_active=False, exit_type=None).values('pk')]

    if exited_run_ids:
        print "Fetched ids of %d completed or restarted runs" % len(exited_run_ids)

        # grab UUIDs of all terminal action sets for quick lookups
        terminal_nodes = set([n['uuid'] for n in ActionSet.objects.filter(destination=None).values('uuid')])
        if terminal_nodes:
            print "Cached %d terminal nodes for run completion calculation" % len(terminal_nodes)

        # pre-fetch required for completion calculation
        steps_prefetch = Prefetch('steps', queryset=FlowStep.objects.order_by('arrived_on'))

        for run in ChunkIterator(FlowRun, exited_run_ids, prefetch_related=(steps_prefetch,)):
            # get last step in this run
            steps = list(run.steps.all())
            last_step = steps[len(steps) - 1] if len(steps) > 0 else None

            if not last_step or step_is_terminal(last_step, terminal_nodes):
                run.exit_type = 'C'
                num_completed += 1
            else:
                run.exit_type = 'R'
                num_restarted += 1

            # use last step arrival as approximate exit time
            run.exited_on = last_step.arrived_on if len(steps) > 0 else None

            run.save(update_fields=('exit_type', 'exited_on'))

            if (num_completed + num_restarted) % 1000 == 0:
                print " > Updated %d of %d runs..." % ((num_completed + num_restarted), len(exited_run_ids))

    if num_expired or num_completed or num_restarted:
        print "Updated run exit states (%d expired, %d completed, %d restarted)" % (num_expired, num_completed, num_restarted)


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0041_flowrun_indexes'),
    ]

    operations = [
        migrations.RenameField(
            model_name='flowrun',
            old_name='expired_on',
            new_name='exited_on',
        ),
        migrations.AlterField(
            model_name='flowrun',
            name='exited_on',
            field=models.DateTimeField(help_text='When the contact exited this flow run', null=True),
        ),
        migrations.AddField(
            model_name='flowrun',
            name='exit_type',
            field=models.CharField(help_text='Why the contact exited this flow run', max_length=1, null=True, choices=[('C', 'Completed'), ('R', 'Restarted'), ('E', 'Expired')]),
        ),
        migrations.RunPython(populate_exit_type)
    ]
