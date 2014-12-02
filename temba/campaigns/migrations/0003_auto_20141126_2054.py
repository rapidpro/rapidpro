# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('flows', '0001_initial'),
        ('contacts', '0001_initial'),
        ('campaigns', '0002_auto_20141126_2054'),
    ]

    operations = [
        migrations.AddField(
            model_name='campaignevent',
            name='flow',
            field=models.ForeignKey(help_text=b'The flow that will be triggered', to='flows.Flow'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='campaignevent',
            name='modified_by',
            field=models.ForeignKey(related_name=b'campaigns_campaignevent_modifications', to=settings.AUTH_USER_MODEL, help_text=b'The user which last modified this item'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='campaignevent',
            name='relative_to',
            field=models.ForeignKey(related_name=b'campaigns', to='contacts.ContactField', help_text=b'The field our offset is relative to'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='campaign',
            name='created_by',
            field=models.ForeignKey(related_name=b'campaigns_campaign_creations', to=settings.AUTH_USER_MODEL, help_text=b'The user which originally created this item'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='campaign',
            name='group',
            field=models.ForeignKey(help_text=b'The group this campaign operates on', to='contacts.ContactGroup'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='campaign',
            name='modified_by',
            field=models.ForeignKey(related_name=b'campaigns_campaign_modifications', to=settings.AUTH_USER_MODEL, help_text=b'The user which last modified this item'),
            preserve_default=True,
        ),
    ]
