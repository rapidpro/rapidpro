# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


def migrate_webhooks(apps, schema_editor):
    from temba.flows.models import Flow

    # get all flows that have a webhook_url in one of their URLs, we want to bring these forward to version 10
    for flow in Flow.objects.filter(is_active=True).exclude(rule_sets__webhook_url=None):
        # migrate this flow forward, this will move webhook_url and webhook_action to the config JSON instead
        flow.ensure_current_version()

class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0064_auto_20160809_1855'),
    ]

    operations = [
        migrations.RunPython(migrate_webhooks)
    ]
