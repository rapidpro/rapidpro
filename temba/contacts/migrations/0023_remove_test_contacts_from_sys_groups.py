# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations

SYS_GROUP_TYPES = ('A', 'B', 'F')


def remove_sim_contacts_from_sys_groups(apps, schema_editor):
    Contact = apps.get_model('contacts', 'Contact')
    wonky = list(Contact.objects.filter(is_test=True, all_groups__group_type__in=SYS_GROUP_TYPES))
    for contact in wonky:
        for group in contact.all_groups.filter(group_type__in=SYS_GROUP_TYPES):
            group.contacts.remove(contact)
            print "Removed test contact #%d from system group #%d (type=%s)" % (contact.pk, group.pk, group.group_type)


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0022_auto_20150815_0003'),
    ]

    operations = [
        migrations.RunPython(remove_sim_contacts_from_sys_groups)
    ]
