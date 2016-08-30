# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import json
import copy
from django.db import migrations, models
from temba.flows.models import FlowException
from temba.flows.flow_migrations import map_actions


def noop(apps, schema):
    pass


def remove_empty_reply_actions(action):
    # this is a reply action, let's see if it is empty
    if action['type'] == 'reply':
        msg = action.get('msg')

        # empty message or dict, delete it
        if not msg:
            return None

        # this is a language dict
        if isinstance(msg, dict):
            # nothing set in any of our languages
            if not any([v for v in msg.values()]):
                return None

    # don't modify this action
    return action


def fix_empty_replies(apps, schema):
    from temba.flows.models import ActionSet

    # iterate across all action sets
    actionsets = ActionSet.objects.all()
    for actionset in actionsets:
        try:
            actionset.get_actions()
        except FlowException as e:
            # this is a broken reply action
            if str(e) in ["Invalid reply action, empty message dict",
                          "Invalid reply action, missing at least one message",
                          "Invalid reply action, no message"]:

                flow = actionset.flow
                print "Flow [%d]: %s" % (flow.id, str(e))

                # get our last definition
                last_revision = flow.revisions.all().order_by('-revision').all().first()
                old_def = json.loads(last_revision.definition)
                new_def = map_actions(copy.deepcopy(old_def), remove_empty_reply_actions)

                # save this as a new revision, with the same version number
                revision = last_revision.revision + 1

                # create a new version
                flow.revisions.create(definition=json.dumps(new_def),
                                      created_by=last_revision.created_by,
                                      modified_by=last_revision.created_by,
                                      spec_version=last_revision.spec_version,
                                      revision=revision)

                print "=" * 50
                print json.dumps(old_def, indent=2)
                print "-" * 50
                print json.dumps(new_def, indent=2)

                # finally, migrate this forward to remove any actionset objects we removed
                flow.ensure_current_version()

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
