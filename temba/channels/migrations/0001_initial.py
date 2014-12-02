# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.conf import settings
import django_countries.fields


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Alert',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text=b'Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text=b'When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text=b'When this item was last modified', auto_now=True)),
                ('alert_type', models.CharField(help_text='The type of alert the channel is sending', max_length=1, verbose_name='Alert Type', choices=[('P', 'Power'), ('D', 'Disconnected'), ('S', 'SMS')])),
                ('ended_on', models.DateTimeField(null=True, verbose_name='Ended On', blank=True)),
                ('host', models.CharField(help_text='The host this alert was created on', max_length=32)),
            ],
            options={
                'abstract': False,
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='Channel',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text=b'Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text=b'When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text=b'When this item was last modified', auto_now=True)),
                ('channel_type', models.CharField(default='A', help_text='Type of this channel, whether Android, Twilio or SMSC', max_length=3, verbose_name='Channel Type', choices=[('A', 'Android'), ('T', 'Twilio'), ('AT', "Africa's Talking"), ('ZV', 'Zenvia'), ('NX', 'Nexmo'), ('IB', 'Infobip'), ('VB', 'Verboice'), ('H9', 'Hub9'), ('VM', 'Vumi'), ('KN', 'Kannel'), ('EX', 'External'), ('TT', 'Twitter'), ('SQ', 'Shaqodoon')])),
                ('name', models.CharField(help_text='Descriptive label for this channel', max_length=64, null=True, verbose_name='Name', blank=True)),
                ('address', models.CharField(help_text='Address with which this channel communicates', max_length=16, null=True, verbose_name='Address', blank=True)),
                ('country', django_countries.fields.CountryField(max_length=2, blank=True, help_text='Country which this channel is for', null=True, verbose_name='Country')),
                ('gcm_id', models.CharField(help_text='The registration id for using Google Cloud Messaging', max_length=255, null=True, verbose_name='GCM ID', blank=True)),
                ('uuid', models.CharField(max_length=36, blank=True, help_text='UUID for this channel', null=True, verbose_name='UUID', db_index=True)),
                ('claim_code', models.CharField(null=True, max_length=16, blank=True, help_text='The token the user will us to claim this channel', unique=True, verbose_name='Claim Code')),
                ('secret', models.CharField(null=True, max_length=64, blank=True, help_text='The secret token this channel should use when signing requests', unique=True, verbose_name='Secret')),
                ('last_seen', models.DateTimeField(help_text='The last time this channel contacted the server', verbose_name='Last Seen', auto_now_add=True)),
                ('device', models.CharField(help_text='The type of Android device this channel is running on', max_length=255, null=True, verbose_name='Device', blank=True)),
                ('os', models.CharField(help_text='What Android OS version this channel is running on', max_length=255, null=True, verbose_name='OS', blank=True)),
                ('alert_email', models.EmailField(help_text='We will send email alerts to this address if experiencing issues sending', max_length=75, null=True, verbose_name='Alert Email', blank=True)),
                ('config', models.TextField(help_text='Any channel specific configuration, used for the various aggregators', null=True, verbose_name='Config')),
                ('role', models.CharField(default='SR', help_text='The roles this channel can fulfill', max_length=4, verbose_name='Channel Role')),
            ],
            options={
                'ordering': ('-last_seen', '-pk'),
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='ChannelLog',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('description', models.CharField(max_length=255)),
                ('is_error', models.BooleanField()),
                ('url', models.TextField(null=True)),
                ('method', models.CharField(max_length=16, null=True)),
                ('request', models.TextField(null=True)),
                ('response', models.TextField(null=True)),
                ('response_status', models.IntegerField(null=True)),
                ('created_on', models.DateTimeField(auto_now_add=True)),
            ],
            options={
            },
            bases=(models.Model,),
        ),
        migrations.CreateModel(
            name='SyncEvent',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text=b'Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text=b'When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text=b'When this item was last modified', auto_now=True)),
                ('power_source', models.CharField(help_text='The power source the device is using', max_length=64, verbose_name='Power Source')),
                ('power_status', models.CharField(default='STATUS_UNKNOWN', help_text='The power status. eg: Charging, Full or Discharging', max_length=64, verbose_name='Power Status')),
                ('power_level', models.IntegerField(help_text='The power level of the battery', verbose_name='Power Level')),
                ('network_type', models.CharField(help_text='The data network type to which the channel is connected', max_length=128, verbose_name='Network Type')),
                ('lifetime', models.IntegerField(default=0, null=True, verbose_name='Lifetime', blank=True)),
                ('pending_message_count', models.IntegerField(default=0, help_text='The number of messages on the channel in PENDING state', verbose_name='Pending Messages Count')),
                ('retry_message_count', models.IntegerField(default=0, help_text='The number of messages on the channel in RETRY state', verbose_name='Retry Message Count')),
                ('incoming_command_count', models.IntegerField(default=0, help_text='The number of commands that the channel gave us', verbose_name='Incoming Command Count')),
                ('outgoing_command_count', models.IntegerField(default=0, help_text='The number of commands that we gave the channel', verbose_name='Outgoing Command Count')),
                ('channel', models.ForeignKey(verbose_name='Channel', to='channels.Channel', help_text='The channel that synced to the server')),
                ('created_by', models.ForeignKey(related_name=b'channels_syncevent_creations', to=settings.AUTH_USER_MODEL, help_text=b'The user which originally created this item')),
                ('modified_by', models.ForeignKey(related_name=b'channels_syncevent_modifications', to=settings.AUTH_USER_MODEL, help_text=b'The user which last modified this item')),
            ],
            options={
                'abstract': False,
            },
            bases=(models.Model,),
        ),
    ]
