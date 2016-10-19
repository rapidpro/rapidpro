# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


def noop(apps, schema):
    pass


def activate_campaign_flows(apps, schema):
    CampaignEvent = apps.get_model('campaigns', 'CampaignEvent')

    # any flow that is archived referenced by an active event
    events = CampaignEvent.objects.filter(is_active=True, flow__is_archived=True).distinct('flow')

    for event in events:
        event.flow.is_archived = False
        event.flow.save()


class Migration(migrations.Migration):

    dependencies = [
        ('campaigns', '0008_auto_20161007_1441'),
    ]

    operations = [
        migrations.RunPython(activate_campaign_flows, noop)
    ]
