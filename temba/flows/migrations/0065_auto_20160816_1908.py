# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


def migrate_webhooks(apps, schema_editor):
    from temba.flows.models import Flow, RuleSet

    # get all flows that have a webhook_url in one of their URLs, we want to bring these forward to version 10
    webhook_rulesets = RuleSet.objects.all().exclude(webhook_url=None).distinct('flow').select_related('flow')
    total = webhook_rulesets.count()
    current = 0

    for ruleset in webhook_rulesets:
        if ruleset.flow.version_number < 10:
            # migrate this flow forward, this will move webhook_url and webhook_action to the config JSON instead
            ruleset.flow.ensure_current_version()

        current += 1
        print "%d / %d flows with webhooks migrated" % (current, total)

class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0064_auto_20160809_1855'),
    ]

    operations = [
        migrations.RunPython(migrate_webhooks)
    ]
