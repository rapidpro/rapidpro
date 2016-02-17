# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('flows', '0050_auto_20160202_1931'),
    ]

    operations = [
        migrations.AddField(
            model_name='flowrun',
            name='submitted_by',
            field=models.ForeignKey(to=settings.AUTH_USER_MODEL,
                                    help_text='The user which submitted this flow run',
                                    null=True),
        ),
    ]
