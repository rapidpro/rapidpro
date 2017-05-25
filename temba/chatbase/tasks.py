from __future__ import print_function, unicode_literals

import logging

from celery.task import task
from temba.orgs.models import Org
from .models import Chatbase


logger = logging.getLogger(__name__)


@task(track_started=True, name='send_chatbase_event')
def send_chatbase_event(org, channel, msg, contact):
    try:
        org = Org.objects.get(id=org)
        if org.is_connected_to_chatbase():
            chatbase_args = dict(org=org.id, channel=channel, msg=msg, contact=contact)
            chatbase = Chatbase.create(**chatbase_args)
            chatbase.trigger_chatbase_event()
    except Exception as e:
        logger.error("Error for chatbase event: %s" % e.args, exc_info=True)
