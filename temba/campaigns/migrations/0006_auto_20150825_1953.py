# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from temba.campaigns.models import CampaignEvent, EventFire

class Migration(migrations.Migration):

    def recalculate_event_fires(apps, schema_editor):
        for event in CampaignEvent.objects.filter(is_active=True).select_related('campaign').order_by('campaign__org'):
            EventFire.do_update_eventfires_for_event(event)

    dependencies = [
        ('campaigns', '0005_auto_20150604_0723'),
    ]

    operations = [
        migrations.RunPython(
            recalculate_event_fires
        )
    ]
