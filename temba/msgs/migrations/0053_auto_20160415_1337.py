# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import requests

from django.conf import settings
from django.core.files import File
from django.core.files.storage import default_storage
from django.core.files.temp import NamedTemporaryFile
from django.db import migrations, models
from django.db.models import Max
from uuid import uuid4


def save_media(org_id, media_url):
    """
    Downloads the url to our org media directory and returns an absolute url to the result
    """
    response = requests.get(media_url, stream=True)
    temp = NamedTemporaryFile(delete=True)
    temp.write(response.content)
    temp.flush()

    file = File(temp)
    random_file = str(uuid4())
    random_dir = random_file[0:4]
    filename = '%s/%s.wav' % (random_dir, random_file)
    path = '%s/%d/media/%s' % (settings.STORAGE_ROOT_DIR, org_id, filename)
    location = default_storage.save(path, file)
    return "audio/x-wav:https://%s/%s" % (settings.AWS_BUCKET_DOMAIN, location)


def do_update(Msg, batch_size=50000, msg_start=0):

    if getattr(Msg, 'objects', None):
        msgs = Msg.objects
    else:
        msgs = Msg.current_messages

    max_pk = msgs.aggregate(Max('pk'))['pk__max']
    if max_pk is not None:
        print "Updating media (start %d)" % msg_start
        for offset in range(msg_start, max_pk + 1, batch_size):
            print 'Batch %d of %d' % (offset, max_pk)

            recordings = msgs.filter(pk__gte=offset,
                                     pk__lt=offset + batch_size,
                                     media__isnull=False)

            to_update = recordings.count()
            if to_update:
                print "  Updating %d rows in this batch" % to_update

            for msg in recordings:
                if msg.media and msg.media.startswith('http'):
                    print '  Old: %s' % msg.media
                    # inbound messages get downloaded to our new location
                    if msg.direction == 'I':
                        url = save_media(msg.org.pk, msg.media)
                        msg.media = url
                        print '  < %s' % (url)

                    # outbound just get prepended with our content type
                    else:
                        msg.media = 'audio/x-wav:%s' % msg.media
                        print '  > %s' % (msg.media)

                    msg.save()


def update_recording_url(apps, schema):
    do_update(apps.get_model('msgs', 'Msg'))


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0052_auto_20160415_1328'),
    ]

    operations = [
        migrations.RunPython(update_recording_url)
    ]
