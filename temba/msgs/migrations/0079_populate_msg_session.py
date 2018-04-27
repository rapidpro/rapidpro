# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.db import migrations
from django.db.models import F, Q


def fix_open_sessions(ChannelSession):

    # session statuses for done
    done = ['D', 'B', 'F', 'N', 'C', 'X']

    # update our erroneously open sessions
    open_sessions = ChannelSession.objects.filter(status__in=done, ended_on=None).exclude(started_on=None)
    updated = open_sessions.update(ended_on=F('modified_on'))
    print('Updated %d open sessions' % updated)


def do_populate(ChannelSession, Msg):
    # update the messages for our session to reference us
    sessions = ChannelSession.objects.filter(is_active=True).exclude(started_on=None).exclude(ended_on=None).order_by('created_on')
    updated = 0
    count = sessions.count()
    for idx, session in enumerate(sessions):
        msgs = Msg.objects.filter(session=None,
                                  contact=session.contact,
                                  created_on__gte=session.started_on,
                                  created_on__lte=session.ended_on)
        updated += msgs.filter(Q(msg_type='V') | Q(channel__channel_type='VMU')).update(session=session)
        if idx % 1000 == 0:
            print("Populated %d of %d sessions (%d msgs)" % (idx, count, updated))
            updated = 0


def apply_as_migration(apps, schema_editor):
    ChannelSession = apps.get_model('channels', 'ChannelSession')
    Msg = apps.get_model('msgs', 'Msg')
    fix_open_sessions(ChannelSession)
    do_populate(ChannelSession, Msg)


def apply_manual():
    from temba.channels.models import ChannelSession
    from temba.msgs.models import Msg
    fix_open_sessions(ChannelSession)
    do_populate(ChannelSession, Msg)


class Migration(migrations.Migration):
    dependencies = [
        ('msgs', '0078_msg_session'),
        ('channels', '0057_remove_channelsession_parent_and_flow')
    ]

    operations = [
        migrations.RunPython(apply_as_migration)
    ]
