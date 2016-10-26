# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import json

import math

from django.contrib.auth.models import User
from django.db import migrations, models
from copy import deepcopy

from temba.flows.models import Flow, Org


def localize_campaign_events(apps, schema_editor):
    CampaignEvent = apps.get_model("campaigns", "CampaignEvent")
    events = CampaignEvent.objects.filter(event_type='M')

    total = events.count()
    for idx, event in enumerate(events):
        if idx % 100 == 0:
            pct = float(idx) / float(total)
            print 'On %d of %d (%d%%)' % (idx+1, total, math.floor(pct * 100))

        flow = event.flow
        flow.is_active = False
        flow.save()

        prev_message = event.message

        try:
            message_dict = json.loads(prev_message)

            message_text = deepcopy(message_dict)

            while isinstance(message_text, dict) and len(message_text.keys()) == 1 and 'base' in message_text:
                message_text = message_text['base']

            next_message = message_text
            if not isinstance(next_message, dict):
                next_message = dict(base=next_message)

            event.message = json.dumps(next_message)
            event.save()

            org = Org.objects.get(id=event.campaign.org_id)
            user = User.objects.get(id=event.created_by_id)

            Flow.create_single_message(org,
                                       user,
                                       next_message)

        except:
            print prev_message
            print event.pk
            print "=" * 40


class Migration(migrations.Migration):

    dependencies = [
        ('campaigns', '0009_auto_20161019_1608'),
    ]

    operations = [
        migrations.RunPython(localize_campaign_events),
    ]

