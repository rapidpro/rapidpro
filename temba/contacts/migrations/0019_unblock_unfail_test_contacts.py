# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations
from django.db.models import Q


def fix_sim_contacts(apps, schema_editor):
    Contact = apps.get_model('contacts', 'Contact')
    wonky = Contact.objects.filter(is_test=True).filter(Q(is_failed=True) | Q(is_blocked=True))
    updated = wonky.update(is_failed=False, is_blocked=False)
    if updated:
        print "Fixed %d test contacts that are blocked or failed" % updated


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0018_update_group_triggers'),
    ]

    operations = [
        migrations.RunPython(fix_sim_contacts)
    ]
