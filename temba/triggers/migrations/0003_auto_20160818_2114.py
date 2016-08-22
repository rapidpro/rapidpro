# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('triggers', '0002_auto_20160706_1921'),
    ]

    operations = [
        migrations.AlterField(
            model_name='trigger',
            name='flow',
            field=models.ForeignKey(related_name='triggers', verbose_name='Flow', to='flows.Flow', help_text='Which flow will be started'),
        ),
    ]
