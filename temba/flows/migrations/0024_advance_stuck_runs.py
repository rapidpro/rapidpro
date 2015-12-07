# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations

def advance_stuck_runs(apps, schema_editor):

    # this data migration is not forward-compatible
    from temba.flows.models import Flow, FlowStep, FlowRun, RuleSet
    from temba.msgs.models import Msg

    flows = Flow.objects.filter(flow_type='F', version_number=5)

    if flows:
        print "%d version 5 flows" % len(flows)

        for flow in flows:

            # looking for flows that start with a passive ruleset
            ruleset = RuleSet.objects.filter(uuid=flow.entry_uuid, flow=flow).first()

            if ruleset and not ruleset.is_pause():

                # now see if there are any active steps at our current flow
                steps = FlowStep.objects.filter(run__is_active=True, step_uuid=ruleset.uuid, rule_value=None, left_on=None).select_related('contact')

                if steps:
                    print '\nAdvancing %d steps for %s:%s' % (len(steps), flow.org.name, flow.name)
                    for idx, step in enumerate(steps):

                        if (idx+1) % 100 == 0:
                            print '\n\n *** Step %d of %d\n\n' % (idx+1, len(steps))

                        # force them to be handled again
                        msg = Msg(contact=step.contact, text='', id=0)
                        Flow.handle_destination(ruleset, step, step.run, msg)


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0023_new_split_dialog'),
    ]

    operations = [
        migrations.RunPython(advance_stuck_runs)
    ]
