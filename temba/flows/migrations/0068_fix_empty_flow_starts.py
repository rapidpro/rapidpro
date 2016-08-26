# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.db.models import Q
import json


def fix_empty_starts(apps, schema):
    empty_actions = ('[{"msg": {"eng": ""}, "type": "reply"}]', '[{"msg": {"base": ""}, "type": "reply"}]')

    from temba.flows.models import ActionSet
    # find any action sets that have no msg body
    empty_actionsets = ActionSet.objects.filter(actions__in=empty_actions).distinct('flow')
    for i, actionset in enumerate(empty_actionsets):
        flow = actionset.flow
        old_def = flow.as_json()

        for actionset in flow.action_sets.all():
            if actionset.actions in empty_actions:
                print "removing: %s" % actionset.as_json()
                actionset.delete()

        # set our entry uuid to the highest node
        highest_action = flow.action_sets.all().order_by('-y').first()
        highest_ruleset = flow.rule_sets.all().order_by('-y').first()

        entry_uuid = None
        if highest_action and highest_ruleset:
            if highest_action.y <= highest_ruleset.y:
                entry_uuid = highest_action.uuid
            else:
                entry_uuid = highest_ruleset.uuid

        elif highest_action and not highest_ruleset:
            entry_uuid = highest_action.uuid
        elif highest_ruleset and not highest_action:
            entry_uuid = highest_ruleset.uuid

        # save our new entry uuid
        flow.entry_uuid = entry_uuid
        flow.save(update_fields=['entry_uuid'])

        print "=" * 50
        print json.dumps(old_def, indent=2)
        print "-" * 50
        print json.dumps(flow.as_json(), indent=2)

        # and create our revision
        flow.update(flow.as_json())

        print "updated %d of %d actionsets" % (i+1, len(empty_actionsets))


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0067_flowstart_extra'),
    ]

    operations = [
        migrations.RunPython(fix_empty_starts)
    ]
