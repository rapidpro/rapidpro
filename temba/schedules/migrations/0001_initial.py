# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Schedule',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('is_active', models.BooleanField(default=True, help_text=b'Whether this item is active, use this instead of deleting')),
                ('created_on', models.DateTimeField(help_text=b'When this item was originally created', auto_now_add=True)),
                ('modified_on', models.DateTimeField(help_text=b'When this item was last modified', auto_now=True)),
                ('status', models.CharField(default='U', max_length=1, choices=[('U', 'Unscheduled'), ('S', 'Scheduled')])),
                ('repeat_hour_of_day', models.IntegerField(help_text='The hour of the day', null=True)),
                ('repeat_day_of_month', models.IntegerField(help_text='The day of the month to repeat on', null=True)),
                ('repeat_period', models.CharField(help_text='When this schedule repeats', max_length=1, null=True, choices=[('O', 'Never'), ('D', 'Daily'), ('W', 'Weekly'), ('M', 'Monthly')])),
                ('repeat_days', models.IntegerField(default=0, help_text='bit mask of days of the week', null=True, blank=True)),
                ('last_fire', models.DateTimeField(default=None, help_text='When this schedule last fired', null=True, blank=True)),
                ('next_fire', models.DateTimeField(default=None, help_text='When this schedule fires next', null=True, blank=True)),
                ('created_by', models.ForeignKey(related_name=b'schedules_schedule_creations', to=settings.AUTH_USER_MODEL, help_text=b'The user which originally created this item')),
                ('modified_by', models.ForeignKey(related_name=b'schedules_schedule_modifications', to=settings.AUTH_USER_MODEL, help_text=b'The user which last modified this item')),
            ],
            options={
                'abstract': False,
            },
            bases=(models.Model,),
        ),
    ]
