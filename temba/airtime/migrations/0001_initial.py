# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0033_auto_20160718_2045'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('contacts', '0041_indexes_update'),
        ('orgs', '0020_auto_20160726_1510'),
    ]

    operations = [
        migrations.CreateModel(
            name='AirtimeTransfer',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text='Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text='When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text='When this item was last modified', auto_now=True)),
                ('status', models.CharField(default=b'P', help_text=b'The state this event is currently in', max_length=1, choices=[(b'P', b'Pending'), (b'C', b'Complete'), (b'F', b'Failed')])),
                ('recipient', models.CharField(max_length=64)),
                ('amount', models.FloatField()),
                ('denomination', models.CharField(max_length=32, null=True, blank=True)),
                ('data', models.TextField(default=b'', null=True, blank=True)),
                ('response', models.TextField(default=b'', null=True, blank=True)),
                ('message', models.CharField(help_text=b'A message describing the end status, error messages go here', max_length=255, null=True, blank=True)),
                ('channel', models.ForeignKey(blank=True, to='channels.Channel', help_text=b'The channel that this airtime is relating to', null=True)),
                ('contact', models.ForeignKey(help_text=b'The contact that this airtime is sent to', to='contacts.Contact')),
                ('created_by', models.ForeignKey(related_name='airtime_airtimetransfer_creations', to=settings.AUTH_USER_MODEL, help_text='The user which originally created this item')),
                ('modified_by', models.ForeignKey(related_name='airtime_airtimetransfer_modifications', to=settings.AUTH_USER_MODEL, help_text='The user which last modified this item')),
                ('org', models.ForeignKey(help_text=b'The organization that this airtime was triggered for', to='orgs.Org')),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
