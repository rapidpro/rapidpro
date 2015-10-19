# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.db.models import Count, Q


def unblock_contacts_imported_again(apps, schema_editor):
    Contact = apps.get_model('contacts', 'Contact')

    blocked_contacts = Contact.objects.filter(is_blocked=True, is_test=False).annotate(group_count=Count('all_groups'))
    reimported_contacts = blocked_contacts.filter(Q(group_count__gt=1) | Q(group_count__lt=1))

    updated = Contact.objects.filter(pk__in=reimported_contacts).update(is_blocked=False)

    if updated:
        print "Fixed %d contacts that are blocked and has another group" % updated


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0024_exportcontactstask_uuid'),
    ]

    operations = [
        migrations.RunPython(unblock_contacts_imported_again)
    ]
