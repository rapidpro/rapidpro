# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0009_auto_20150210_1751'),
    ]

    operations = [
        migrations.AlterField(
            model_name='exportflowresultstask',
            name='org',
            field=models.ForeignKey(related_name='flow_results_exports', to='orgs.Org', help_text='The Organization of the user.'),
            preserve_default=True,
        ),
    ]
