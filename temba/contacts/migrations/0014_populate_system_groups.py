# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    def create_virtual_groups(apps, schema_editor):
        Org = apps.get_model('orgs', 'Org')
        ContactGroup = apps.get_model('contacts', 'ContactGroup')
        Contact = apps.get_model('contacts', 'Contact')

        # creates and populates all our system groups
        for org in Org.objects.all():
            #print "Populating groups for: %s" % org.name

            all_contacts = ContactGroup.objects.create(name="All Contacts",
                                                       group_type='A',
                                                       created_by=org.created_by,
                                                       modified_by=org.modified_by,
                                                       org=org)
            all_contacts.contacts.add(*Contact.objects.filter(org=org, is_active=True, is_test=False,
                                                              is_blocked=False))

            blocked_contacts = ContactGroup.objects.create(name="Blocked Contacts",
                                                           group_type='B',
                                                           created_by=org.created_by,
                                                           modified_by=org.modified_by,
                                                           org=org)
            blocked_contacts.contacts.add(*Contact.objects.filter(org=org, is_active=True, is_test=False,
                                                                  is_blocked=True))

            failed_contacts = ContactGroup.objects.create(name="Failed Contacts",
                                                          group_type='F',
                                                          created_by=org.created_by,
                                                          modified_by=org.modified_by,
                                                          org=org)
            failed_contacts.contacts.add(*Contact.objects.filter(org=org, is_active=True, is_test=False,
                                                                 is_blocked=False, is_failed=True))
    dependencies = [
        ('orgs', '0003_auto_20150313_1624'),
        ('contacts', '0013_contactgroup_group_type')
    ]

    operations = [
        migrations.RunPython(
            create_virtual_groups,
        ),
    ]
