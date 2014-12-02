# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('ivr', '0001_initial'),
        ('flows', '0001_initial'),
        ('orgs', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='ivrcall',
            name='org',
            field=models.ForeignKey(help_text='The organization this call belongs to', to='orgs.Org'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='ivraction',
            name='call',
            field=models.ForeignKey(related_name='ivr_actions_for_call', to='ivr.IVRCall', help_text='The call this action is a part of'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='ivraction',
            name='org',
            field=models.ForeignKey(related_name='ivr_actions', to='orgs.Org', help_text='The org this message is connected to'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='ivraction',
            name='step',
            field=models.ForeignKey(related_name='ivr_actions_for_step', blank=True, to='flows.FlowStep', help_text='The step that created this action', null=True),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='ivraction',
            name='topup',
            field=models.ForeignKey(related_name='ivr', on_delete=django.db.models.deletion.SET_NULL, blank=True, to='orgs.TopUp', help_text='The topup that this action was deducted from', null=True),
            preserve_default=True,
        ),
    ]
