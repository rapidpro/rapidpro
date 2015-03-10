# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os

from django.core.files.storage import default_storage
from django.db import migrations
from temba.assets import AssetType
from temba.orgs.models import Org


def migrate_contact_exports(apps, schema_editor):
    ExportContactsTask = apps.get_model('contacts', 'ExportContactsTask')

    handler = AssetType.contact_export.get_handler()

    num_copied = 0
    num_missing = 0

    for task in ExportContactsTask.objects.select_related('created_by').all():
        if not task.filename:
            num_missing += 1
            continue

        identifier = task.pk
        existing_ext = os.path.splitext(task.filename)[1][1:]

        task.org = Org.objects.get(pk=task.org_id)  # replace with actual org instance with functions

        existing_file = default_storage.open(task.filename)
        handler.save(identifier, existing_file, existing_ext)
        num_copied += 1

    print 'Copied %d contact export files (%d tasks have no file)' % (num_copied, num_missing)


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0005_auto_20141210_0208'),
    ]

    operations = [
        migrations.RunPython(migrate_contact_exports)
    ]
