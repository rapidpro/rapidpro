# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations


def release_inactive_contact_calls(apps, schema_editor):
    """
    Calls belonging to inactive contacts should also be inactive
    """
    Call = apps.get_model('msgs', 'Call')
    updated = Call.objects.filter(contact__is_active=False).update(is_active=False)
    if updated:
        print("Deactivated %d calls belonging to inactive contacts" % updated)


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0022_no_archived_outgoing'),
    ]

    operations = [
        migrations.RunPython(release_inactive_contact_calls)
    ]
