# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0030_auto_20150825_1406'),
        ('contacts', '0021_auto_20150727_0727'),
    ]

    def finish_completed_runs(apps, schema_editor):
        """
        Updates any flow runs that are sitting at a terminal actionset as being completed.
        """
        from temba.flows.models import FlowRun, Flow, FlowStep

        # get all the flows that have active runs
        active_flows = FlowRun.objects.filter(is_active=True).order_by('flow_id').values('flow_id').distinct('flow_id')

        completed = 0

        for active_flow in active_flows:
            flow = Flow.objects.get(pk=active_flow['flow_id'])

            print "Updating runs for %s" % flow.name

            # get any terminal action sets for this flow
            terminal_actions = [node.uuid for node in flow.action_sets.filter(destination=None)]

            # get any steps at these terminal nodes
            active_terminal_steps = FlowStep.objects.filter(run__is_active=True, run__flow=flow,
                                                            left_on=None, step_uuid__in=terminal_actions)\
                                                    .select_related('run', 'run__flow', 'run__contact')

            # mark each of these runs as completed, this takes care of updating redis status appropriately as well
            for terminal_step in active_terminal_steps:
                terminal_step.run.set_completed(terminal_step)
                print "."
                completed += 1

        if completed:
            print "Completed %d flow runs." % completed


    operations = [
        migrations.RunPython(finish_completed_runs)
    ]
