# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.utils import timezone
from datetime import timedelta

class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0036_auto_20151014_1703'),
    ]

    def fix_date_tests(apps, schema_editor):
        from temba.flows.models import RuleSet
        from temba.flows.models import DateTest
        from temba.utils.expressions import replace_filter_style

        last_two_days = timezone.now() - timedelta(days=3)

        for ruleset in RuleSet.objects.filter(modified_on__gte=last_two_days):
            changed = False
            rules = ruleset.get_rules()
            for rule in rules:
                if isinstance(rule.test, DateTest):
                    if rule.test.test.find('time_delta') > 0:
                        old = rule.test.test
                        rule.test.test = replace_filter_style(rule.test.test)
                        changed = True
                        print "migrated '%s' to '%s'" % (old, rule.test.test)

            if changed:
                ruleset.set_rules(rules)
                ruleset.save()

    def noop(apps, schema_editor):
        pass

    operations = [
        migrations.RunPython(fix_date_tests, noop)
    ]
