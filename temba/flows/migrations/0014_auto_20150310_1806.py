# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from uuid import uuid4

from django.db import models, migrations
from temba.flows.models import RuleSet, TrueTest, Rule
from django.utils import timezone
import pytz
import json


def fix_missing_other_rule(apps, schema_editor):

    # Excuted migration actually used real model, to allow below
    # method call to flow.update(). With the model
    # definition changing at 0023_new_split_dialog we can no longer
    # fetch this model. This data migration will be removed
    # when we squash for the first community release.

    # from temba.flows.models import Flow
    Flow = apps.get_model('flows', 'Flow')

    # only consider flows with a version created after previous migration
    start_time = timezone.datetime(2015, 3, 2, 20, 20, 0, 0, tzinfo=pytz.utc)

    FlowVersion = apps.get_model('flows', 'FlowVersion')
    for version in FlowVersion.objects.filter(created_on__gt=start_time).order_by('flow__pk', '-pk').distinct('flow__pk'):

        for ruleset in version.flow.rule_sets.all():
            rules = ruleset.get_rules()

            # identify current rulesets missing other rule
            has_other_rule = False
            for rule in rules:
                if isinstance(rule.test, TrueTest):
                    has_other_rule = True

            if not has_other_rule:

                print '-' * 20
                print "[%d] %s - %s [%s] - %s (missing other rule)" % (version.flow.pk, version.flow.org.name, version.flow.name, ruleset.label, ruleset.uuid)

                # search for our previous other rule on this ruleset
                previous_other_rule = None

                # lookup the last known other rule for this ruleset
                previous_versions = version.flow.versions.filter(pk__lt=version.pk).order_by('-pk')

                print "Most recent version - (%s)" % version.created_on

                for idx, previous_version in enumerate(previous_versions):

                    if previous_other_rule:
                        break

                    print "Considering version %d (%s)" % (idx, previous_version.created_on)

                    previous_definition = json.loads(previous_version.definition)
                    for previous_ruleset in previous_definition['rule_sets']:

                        if previous_other_rule:
                            break

                        if previous_ruleset['uuid'] == ruleset.uuid:
                            for previous_rule in previous_ruleset['rules']:
                                if previous_rule['test']['type'] == 'true' and previous_rule.get('destination', None):
                                    print "Using old destination (%s) -> %s" % (previous_version.created_on, previous_rule['destination'])
                                    previous_other_rule = previous_rule
                                    break

                    # only want to consider at most one version before our start time
                    if previous_version.created_on < start_time:
                        break

                if previous_other_rule:
                    rules.append(Rule(previous_other_rule['uuid'],
                                      previous_other_rule['category'],
                                      previous_other_rule['destination'],
                                      TrueTest()))
                else:
                    print "No acceptable versions. Creating empty other rule."

                    other_name = 'Other'
                    if len(rules) == 0:
                        other_name = 'All Responses'

                    # force into a dict if localized
                    base_language = version.flow.base_language
                    if base_language:
                        other_name = {base_language: other_name}

                    rules.append(Rule(unicode(uuid4()), other_name, None, TrueTest()))

                # save off our ruleset
                ruleset.set_rules(rules)
                ruleset.save()

                # and force a new revision to mark the change
                version.flow.update(version.flow.as_json())


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0013_auto_20150306_1817'),
    ]

    operations = [
        migrations.RunPython(fix_missing_other_rule)
    ]
