# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.core.urlresolvers import reverse
from django.conf import settings
from django.db import migrations
from temba.orgs.models import Org, NEXMO_UUID, NEXMO_KEY, NEXMO_SECRET, NEXMO_APP_ID


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0058_add_junebug_channel_type'),
    ]

    def update_nexmo_channels_roles(apps, schema_editor):
        Channel = apps.get_model('channels', 'Channel')

        if settings.IS_PROD:
            nexmo_channels = Channel.objects.filter(is_active=True, channel_type='NX')

            updated = []
            updated_orgs = []
            for channel in nexmo_channels:
                try:
                    org = Org.objects.get(pk=channel.org_id)

                    if org.pk not in updated_orgs:
                        org_uuid = org.config_json().get(NEXMO_UUID)
                        nexmo_api_key = org.config_json().get(NEXMO_KEY, None)
                        nexmo_secret = org.config_json().get(NEXMO_SECRET, None)

                        org.connect_nexmo(nexmo_api_key, nexmo_secret, org.created_by)
                        org.refresh_from_db()

                        updated_orgs.append(org.pk)

                        app_id = org.config_json().get(NEXMO_APP_ID, None)

                        nexmo_client = org.get_nexmo_client()

                        mo_path = reverse('handlers.nexmo_handler', args=['receive', org_uuid])

                        nexmo_client.update_nexmo_number(channel.country, channel.address,
                                                         'http://%s%s' % (settings.HOSTNAME, mo_path),
                                                         app_id)

                    updated.append(channel.id)
                except Exception:
                    pass

            # change role for those we successfully updated the callback URLs
            Channel.objects.filter(id__in=updated).update(role='SRCA')

    operations = [
        migrations.RunPython(update_nexmo_channels_roles)
    ]
