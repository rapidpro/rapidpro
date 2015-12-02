# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations
from temba.flows.models import RuleSet, ActionSet


def fix_like_named_destinations(apps, schema_editor):

    # Excuted migration actually used real model, to allow below
    # method call to flow.update(). With the model
    # definition changing at 0023_new_split_dialog we can no longer
    # fetch this model. This data migration will be removed
    # when we squash for the first community release.

    # from temba.flows.models import Flow
    Flow = apps.get_model('flows', 'Flow')

    # must have a label
    updated_rules = RuleSet.objects.filter(label=None).update(label='Response A')
    if updated_rules:
        print "%d empty labels updated" % updated_rules

    updates = 0
    for flow in Flow.objects.all():

        for ruleset in RuleSet.objects.filter(flow=flow):
            actionsets = {actionset['uuid'] for actionset in ActionSet.objects.filter(flow=flow).values('uuid')}

            category_map = {}
            new_rules = []
            rules = ruleset.get_rules()
            for rule in rules:
                category_name = rule.get_category_name(flow.base_language)

                if not category_name:
                    continue

                category_name = category_name.lower()
                category_map[category_name] = rule.destination

            changed = False
            for rule in rules:
                category_name = rule.get_category_name(flow.base_language)

                if not category_name:
                    changed = True
                    print "[%s] - %d: (No Category) (%s)" % (flow.org.name, flow.pk, flow.modified_on)
                    continue

                if rule.destination and rule.destination not in actionsets:
                    print ("*" * 8) + " Fixing missing actionset" + ("*" * 8)
                    rule.destination = None
                    changed = True

                new_destination = category_map[category_name.lower()]
                if new_destination != rule.destination:
                    print "[%s] - %d: %s (%s)" % (flow.org.name, flow.pk, rule.get_category_name(flow.base_language), flow.modified_on)
                    changed = True
                    rule.destination = new_destination

                new_rules.append(rule)

            if changed:
                try:
                    flow.update(flow.as_json())
                except Exception:
                    print "Skipping backup of broken flow [%d] - %s - %s" % (flow.pk, flow.org.name, flow.name)

                ruleset.set_rules(new_rules)
                updates += 1

    if updates:
        print "Updated %d flows" % updates


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0010_auto_20150210_1845'),
    ]

    operations = [
        migrations.RunPython(fix_like_named_destinations)
    ]
