# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('orgs', '0008_update_maybe_squash'),
    ]

    operations = [
        migrations.AddField(
            model_name='org',
            name='surveyors',
            field=models.ManyToManyField(help_text='The users can login via Android for your organization', related_name='org_surveyors', verbose_name='Surveyors', to=settings.AUTH_USER_MODEL),
            preserve_default=True,
        ),
    ]
