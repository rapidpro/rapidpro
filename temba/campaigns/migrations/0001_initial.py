# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Campaign',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text=b'Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text=b'When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text=b'When this item was last modified', auto_now=True)),
                ('name', models.CharField(help_text=b'The name of this campaign', max_length=255)),
                ('is_archived', models.BooleanField(default=False, help_text=b'Whether this campaign is archived or not')),
            ],
            options={
                'abstract': False,
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='CampaignEvent',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text=b'Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text=b'When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text=b'When this item was last modified', auto_now=True)),
                ('offset', models.IntegerField(default=0, help_text=b'The offset in days from our date (positive is after, negative is before)')),
                ('unit', models.CharField(default=b'D', help_text=b'The unit for the offset for this event', max_length=1, choices=[(b'M', b'Minutes'), (b'H', b'Hours'), (b'D', b'Days'), (b'W', b'Weeks')])),
                ('event_type', models.CharField(default=b'F', help_text=b'The type of this event', max_length=1, choices=[(b'F', b'Flow Event'), (b'M', b'Message Event')])),
                ('message', models.TextField(help_text=b'The message to send out', null=True, blank=True)),
                ('delivery_hour', models.IntegerField(default=-1, help_text=b'The hour to send the message or flow at.')),
            ],
            options={
                'abstract': False,
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='EventFire',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('scheduled', models.DateTimeField(help_text=b'When this event is scheduled to run')),
                ('fired', models.DateTimeField(help_text=b'When this event actually fired, null if not yet fired', null=True, blank=True)),
            ],
            options={
                'ordering': ('scheduled',),
            },
            bases=(models.Model,),
        ),
    ]
