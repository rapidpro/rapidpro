# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.db.models import Count
from temba.utils import chunk_list

class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0038_install_contact_triggers'),
    ]

    def derive_opt_outs(apps, schema_editor):
        from temba.contacts.models import Contact, ContactGroup

        # remap our group types to reflect failed becoming stopped
        ContactGroup.system_groups.filter(group_type='F').update(group_type='S')

        # now unstop any contacts that belong to groups, these are temporary failures
        failed_ids = Contact.objects.filter(is_active=True, is_stopped=True, all_groups__group_type='U').distinct().values_list('id', flat=True)
        for chunk_ids in chunk_list(failed_ids, 100):
            contacts = Contact.objects.filter(id__in=chunk_ids)
            for contact in contacts:
                contact.unstop(contact.modified_by)
                print "unstopped: %d" % contact.id

    operations = [
        migrations.RunPython(derive_opt_outs)
    ]
