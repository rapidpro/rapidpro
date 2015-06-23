# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from temba.utils.models import generate_uuid


def populate_campaign_uuid(apps, schema_editor):
    model = apps.get_model("campaigns", "Campaign")
    for obj in model.objects.all():
        obj.uuid = generate_uuid()
        obj.save(update_fields=('uuid',))


def populate_campaign_event_uuid(apps, schema_editor):
    model = apps.get_model("campaigns", "CampaignEvent")
    for obj in model.objects.all():
        obj.uuid = generate_uuid()
        obj.save(update_fields=('uuid',))


class Migration(migrations.Migration):

    dependencies = [
        ('campaigns', '0004_campaign_org'),
    ]

    operations = [
        migrations.AddField(
            model_name='campaign',
            name='uuid',
            field=models.CharField(max_length=36, help_text='The unique identifier for this object', null=True, verbose_name='Unique Identifier'),
            preserve_default=True,
        ),
        migrations.RunPython(
            populate_campaign_uuid
        ),
        migrations.AlterField(
            model_name='campaign',
            name='uuid',
            field=models.CharField(default=generate_uuid, max_length=36, help_text='The unique identifier for this object', unique=True, verbose_name='Unique Identifier'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='campaignevent',
            name='uuid',
            field=models.CharField(max_length=36, help_text='The unique identifier for this object', null=True, verbose_name='Unique Identifier'),
            preserve_default=True,
        ),
        migrations.RunPython(
            populate_campaign_event_uuid
        ),
        migrations.AlterField(
            model_name='campaignevent',
            name='uuid',
            field=models.CharField(default=generate_uuid, max_length=36, help_text='The unique identifier for this object', unique=True, verbose_name='Unique Identifier'),
            preserve_default=True,
        ),
    ]
