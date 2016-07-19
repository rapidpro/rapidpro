from django.db import models


from smartmin.models import SmartModel
from temba.channels.models import Channel
from temba.contacts.models import Contact, ContactURN
from temba.orgs.models import Org


class Airtime(SmartModel):
    TRANSFERTO_AIRTIME_API_URL = 'https://fm.transfer-to.com/cgi-bin/shop/topup'
    LOG_DIVIDER = "%s\n\n\n" % ('=' * 20)

    PENDING = 'P'
    COMPLETE = 'C'
    FAILED = 'F'

    STATUS_CHOICES = ((PENDING, "Pending"),
                      (COMPLETE, "Complete"),
                      (FAILED, "Failed"))

    org = models.ForeignKey(Org, help_text="The organization that this airtime was triggered for")

    status = models.CharField(max_length=1, choices=STATUS_CHOICES, default='P',
                              help_text="The state this event is currently in")

    channel = models.ForeignKey(Channel, null=True, blank=True,
                                help_text="The channel that this airtime is relating to")

    contact = models.ForeignKey(Contact, help_text="The contact that this airtime is sent to")

    contact_urn = models.ForeignKey(ContactURN, help_text="The contact URN that this airtime is sent to")

    amount = models.FloatField()

    denomination = models.CharField(max_length=32, null=True, blank=True)

    data = models.TextField(null=True, blank=True, default="")

    response = models.TextField(null=True, blank=True, default="")

    message = models.CharField(max_length=255,
                               help_text="A message describing the end status, error messages go here")
