# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('contacts', '0001_initial'),
        ('campaigns', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='eventfire',
            name='contact',
            field=models.ForeignKey(related_name=b'fire_events', to='contacts.Contact', help_text=b'The contact that is scheduled to have an event run'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='eventfire',
            name='event',
            field=models.ForeignKey(related_name=b'event_fires', to='campaigns.CampaignEvent', help_text=b'The event that will be fired'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='campaignevent',
            name='campaign',
            field=models.ForeignKey(related_name=b'events', to='campaigns.Campaign', help_text=b'The campaign this event is part of'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='campaignevent',
            name='created_by',
            field=models.ForeignKey(related_name=b'campaigns_campaignevent_creations', to=settings.AUTH_USER_MODEL, help_text=b'The user which originally created this item'),
            preserve_default=True,
        ),
    ]
