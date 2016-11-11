# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import math
import json
from django.db import migrations, models


def localize_campaign_events(apps, schema_editor):
    CampaignEvent = apps.get_model("campaigns", "CampaignEvent")
    events = CampaignEvent.objects.filter(event_type='M')

    total = events.count()
    for idx, event in enumerate(events):
        if idx % 100 == 0:
            pct = float(idx) / float(total)
            print 'On %d of %d (%d%%)' % (idx+1, total, math.floor(pct * 100))
        try:
            json.loads(event.message)
        except:
            event.message = json.dumps(dict(base=event.message))
            event.save()


class Migration(migrations.Migration):

    dependencies = [
        ('campaigns', '0007_auto_20160901_2031'),
    ]

    operations = [
        migrations.RunPython(localize_campaign_events),
    ]