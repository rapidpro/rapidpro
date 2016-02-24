# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0049_install_flowcount_triggers'),
    ]

    operations = [
        migrations.AlterField(
            model_name='flowrevision',
            name='created_by',
            field=models.ForeignKey(related_name='flows_flowrevision_creations', to=settings.AUTH_USER_MODEL, help_text='The user which originally created this item'),
        ),
        migrations.AlterField(
            model_name='flowrevision',
            name='flow',
            field=models.ForeignKey(related_name='revisions', to='flows.Flow'),
        ),
        migrations.AlterField(
            model_name='flowrevision',
            name='modified_by',
            field=models.ForeignKey(related_name='flows_flowrevision_modifications', to=settings.AUTH_USER_MODEL, help_text='The user which last modified this item'),
        ),
        migrations.AlterField(
            model_name='flowrevision',
            name='revision',
            field=models.IntegerField(help_text='Revision number for this definition', null=True),
        ),
        migrations.AlterField(
            model_name='flowrun',
            name='exit_type',
            field=models.CharField(help_text='Why the contact exited this flow run', max_length=1, null=True, choices=[('C', 'Completed'), ('I', 'Interrupted'), ('E', 'Expired')]),
        ),
    ]
