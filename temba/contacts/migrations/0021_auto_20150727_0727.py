# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


def rename_language_contactfields(apps, schema_editor):
    ContactField = apps.get_model("contacts", "ContactField")
    for contactfield in ContactField.objects.filter(label__iexact='language'):
        contactfield.label = 'User Language'
        contactfield.save()


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0020_exportcontactstask_is_finished')
    ]

    operations = [
        migrations.RunPython(rename_language_contactfields),
    ]
