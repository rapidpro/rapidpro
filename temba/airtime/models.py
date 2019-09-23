from smartmin.models import SmartModel

from django.db import models

from temba.channels.models import Channel
from temba.contacts.models import Contact
from temba.orgs.models import Org


class AirtimeTransfer(SmartModel):
    STATUS_PENDING = "P"
    STATUS_SUCCESS = "S"
    STATUS_FAILED = "F"
    STATUS_CHOICES = ((STATUS_PENDING, "Pending"), (STATUS_SUCCESS, "Success"), (STATUS_FAILED, "Failed"))

    org = models.ForeignKey(
        Org,
        on_delete=models.PROTECT,
        related_name="airtime_transfers",
        help_text="The organization that this airtime was triggered for",
    )

    status = models.CharField(
        max_length=1, choices=STATUS_CHOICES, default="P", help_text="The state this event is currently in"
    )

    channel = models.ForeignKey(
        Channel,
        on_delete=models.PROTECT,
        related_name="airtime_transfers",
        null=True,
        blank=True,
        help_text="The channel that this airtime is relating to",
    )

    contact = models.ForeignKey(
        Contact, on_delete=models.PROTECT, help_text="The contact that this airtime is sent to"
    )

    recipient = models.CharField(max_length=64)

    amount = models.FloatField()

    denomination = models.CharField(max_length=32, null=True, blank=True)

    data = models.TextField(null=True, blank=True, default="")

    response = models.TextField(null=True, blank=True, default="")

    message = models.CharField(
        max_length=255, null=True, blank=True, help_text="A message describing the end status, error messages go here"
    )
