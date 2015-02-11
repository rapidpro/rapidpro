# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0001_initial'),
        ('flows', '0007_auto_20150115_1926'),
    ]

    operations = [
        migrations.AddField(
            model_name='exportflowresultstask',
            name='org',
            field=models.ForeignKey(related_name='flow_results_exports', to='orgs.Org', help_text='The Organization of the user.', null=True),
            preserve_default=True,
        ),
    ]
