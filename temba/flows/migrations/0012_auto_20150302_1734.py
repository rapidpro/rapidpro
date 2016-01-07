# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations
from temba.flows.models import RuleSet, ActionSet, TrueTest


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

                # prefer first valid destination for like named categories
                if rule.destination in actionsets and category_name not in category_map:
                    category_map[category_name] = rule.destination

            changed = False
            other_rule = None
            for rule in rules:

                category_name = rule.get_category_name(flow.base_language)

                if isinstance(rule.test, TrueTest):
                    other_rule = rule
                    continue

                if not category_name:
                    changed = True
                    print "[%s] - %d: (No Category) (%s)" % (flow.org.name, flow.pk, flow.modified_on)
                    continue

                if rule.destination and rule.destination not in actionsets:
                    print ("*" * 8) + " Fixing missing actionset " + ("*" * 8)
                    rule.destination = None
                    changed = True

                new_destination = None
                if category_name.lower() in category_map:
                    new_destination = category_map[category_name.lower()]

                if new_destination != rule.destination:

                    if new_destination is None:
                        print ("*" * 8)+ " WARNING, clearing destination " + ("*" * 8)

                    print "%s->%s [%s] - %d: %s" % (rule.destination, new_destination, flow.org.name, flow.pk, rule.get_category_name(flow.base_language))
                    changed = True
                    rule.destination = new_destination
                new_rules.append(rule)

            if changed:

                # tack on our other rule
                if other_rule:
                    new_rules.append(other_rule)

                # update our ruleset
                ruleset.set_rules(new_rules)
                ruleset.save()

                # make sure there's nothing cached when creating our new revision
                flow = Flow.objects.get(pk=flow.pk)
                flow.update(flow.as_json())

                updates += 1

    if updates:
        print "Updated %d rulesets" % updates


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0011_auto_20150227_1823'),
    ]

    operations = [
        migrations.RunPython(fix_like_named_destinations)
    ]
