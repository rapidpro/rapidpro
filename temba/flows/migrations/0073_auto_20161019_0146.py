# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


def noop(apps, schema):
    pass


def activate_campaign_flows(apps, schema):
    CampaignEvent = apps.get_model('campaigns', 'Campaign')

    # any flow that is archived referenced by an active event
    events = CampaignEvent.objects.filter(is_active=True, flow__is_archived=True).distinct('flow')

    for event in events:
        event.flow.is_archived = False
        event.flow.save()


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0072_auto_20160905_1537'),
    ]

    operations = [
        migrations.RunPython(activate_campaign_flows, noop)
    ]
