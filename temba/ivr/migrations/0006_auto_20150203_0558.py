# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from temba.msgs.models import Msg, HANDLED, IVR
from temba.orgs.models import Org
from temba.flows.models import FlowStep, ActionSet, SayAction
from django.conf import settings


class Migration(migrations.Migration):
    def reverse(apps, schema_editor):
        IVRAction = apps.get_model("ivr", "IVRAction")
        #
        for org in Org.objects.all():
            channel = org.get_call_channel()
            print "Processing %s" % org
            if channel:
                for ivr in IVRAction.objects.filter(org=org):
                    step = FlowStep.objects.get(pk=ivr.step.pk)
                    if step.rule_value:
                        print "[%s] %s" % (ivr.call.contact_urn, step.rule_value)
                        step.messages.all().delete()
                        print step.messages.all().count()

    def create_messages_for_ivr_actions(apps, schema_editor):
        from django.contrib.auth.models import User

        IVRAction = apps.get_model("ivr", "IVRAction")
        # create a one-to-one mapping for any ivr actions as ivr messages
        for org in Org.objects.all():
            channel = org.get_call_channel()

            # print "Processing %s" % org
            if channel:
                for ivr in IVRAction.objects.filter(org=org):
                    step = FlowStep.objects.get(pk=ivr.step.pk)
                    if step.rule_value:
                        urn = ivr.call.contact_urn
                        msg_dict = {}
                        if step.rule_value[0:4] == 'http':
                            msg_dict['recording_url'] = step.rule_value

                        user = User.objects.get(pk=ivr.call.created_by_id)
                        msg = Msg.create_incoming(channel, (urn.scheme, urn.path), step.rule_value,
                                                  user=user, topup=ivr.topup, status=HANDLED,
                                                  msg_type=IVR, date=ivr.created_on, org=org, **msg_dict)
                        step.add_message(msg)


    dependencies = [
        ('ivr', '0005_auto_20150129_1759'),
        ('msgs', '0003_auto_20150129_0515'),
        ('orgs', '0012_auto_20151026_1152'),
    ]

    operations = [
        migrations.RunPython(create_messages_for_ivr_actions, reverse)
    ]
