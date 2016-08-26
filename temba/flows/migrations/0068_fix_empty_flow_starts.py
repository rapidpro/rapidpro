# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from temba.flows.models import FlowException
import json


def noop(apps, schema):
    pass


def fix_empty_replies(apps, schema):
    from temba.flows.models import ActionSet

    # iterate across all action sets
    actionsets = ActionSet.objects.all()
    for actionset in actionsets:
        try:
            actionset.get_actions()
        except FlowException as e:
            flow = actionset.flow
            old_def = flow.as_json()

            removed_entry = False
            removed_node = False

            # look through all the action sets finding any that won't deserialize
            for actionset in flow.action_sets.all():
                try:
                    actionset.get_actions()
                except FlowException as e:
                    if str(e) in ["Invalid reply action, empty message dict", "Invalid reply action, missing at least one message", "Invalid reply action, no message"]:
                        if actionset.uuid == flow.entry_uuid:
                            removed_entry = True
                        actionset.delete()
                        removed_node = True
                except:
                    pass

            if removed_entry:
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

            if removed_node:
                print "=" * 50
                print json.dumps(old_def, indent=2)
                print "-" * 50
                print json.dumps(flow.as_json(), indent=2)

                # and create our new revision
                try:
                    flow.update(flow.as_json())
                except Exception:
                    import traceback
                    traceback.print_exc()

        # other exceptions during decoding likely are version issues, ignore them
        except:
            pass


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0067_flowstart_extra'),
    ]

    operations = [
        migrations.RunPython(fix_empty_replies, noop)
    ]
