# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from temba.ivr.models import IVRCall
from temba.ivr.models import ContactURN

def populate_unknown_urns(apps, schema_editor):

    IVRCall = apps.get_model('ivr', 'IVRCall')

    # fix any Calls that remain with no contact URN (because their contact doesn't have one)
    for call in IVRCall.objects.filter(contact_urn=None):
        # find or create an unknown contact URN for this org
        unknown = ContactURN.objects.filter(urn='tel:unknown', org=call.org).first()
        if not unknown:
            unknown = ContactURN.objects.create(org=call.org,
                                                scheme='tel',
                                                path='unknown',
                                                urn='tel:unknown',
                                                priority=50)
        call.contact_urn = unknown
        call.save()

class Migration(migrations.Migration):

    dependencies = [
        ('ivr', '0003_auto_20150129_1725'),
    ]

    operations = [

        # populate Call.contact_urn with the Call's contact's tel URN
        migrations.RunSQL("""UPDATE ivr_ivrcall AS s SET contact_urn_id = cu.id FROM contacts_contacturn AS cu
                  WHERE s.contact_id = cu.contact_id AND cu.scheme = 'tel'"""),

        migrations.RunPython(populate_unknown_urns)
    ]
