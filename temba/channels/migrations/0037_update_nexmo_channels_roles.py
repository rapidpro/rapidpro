# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.core.urlresolvers import reverse
from django.conf import settings
from django.db import migrations, models
from temba.orgs.models import Org, NEXMO_UUID


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0036_remove_alert_host'),
    ]

    def update_nexmo_channels_roles(apps, schema_editor):
        Channel = apps.get_model('channels', 'Channel')

        nexmo_channels = Channel.objects.filter(channel_type='NX').exclude(org=None)

        updated = []
        for channel in nexmo_channels:
            try:
                org = Org.objects.get(pk=channel.org_id)
                org_uuid = org.config_json().get(NEXMO_UUID)

                nexmo_client = org.get_nexmo_client()

                mo_path = reverse('handlers.nexmo_handler', args=['receive', org_uuid])
                answer_url = reverse('handlers.nexmo_call_handler', args=['answer', channel.uuid])

                nexmo_client.update_number(channel.country, channel.address,
                                           'http://%s%s' % (settings.TEMBA_HOST, mo_path),
                                           'http://%s%s' % (settings.TEMBA_HOST, answer_url))

                updated.append(channel.id)
            except Exception:
                pass

        # change role for those we successfully updated the callback urls
        Channel.objects.filter(id__in=updated).update(role='SRCA')

    operations = [
        migrations.RunPython(update_nexmo_channels_roles)
    ]
