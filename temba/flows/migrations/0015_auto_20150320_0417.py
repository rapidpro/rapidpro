# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
import json

def fix_unlocalized_flows(apps, schema_editor):
    Flow = apps.get_model("flows", "Flow")
    ActionSet = apps.get_model("flows", "ActionSet")

    for flow in Flow.objects.exclude(base_language=None):

        broken = False
        for action_set in ActionSet.objects.filter(flow=flow):
            actions = json.loads(action_set.actions)

            for action in actions:
                if action['type'] in ('send', 'reply', 'say'):

                    # see if our message isn't localized
                    if not isinstance(action['msg'], dict):
                        broken = True

                    # do the same for the recordings
                    if 'url' in action and not isinstance(action['url'], dict):
                        broken = True

        if broken:
            print "[%d] %s: Removing language (%s)" % (flow.pk, flow.name, flow.base_language)
            flow.base_language = None
            flow.save()

class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0014_auto_20150310_1806'),
    ]

    operations = [
        migrations.RunPython(fix_unlocalized_flows)
    ]
