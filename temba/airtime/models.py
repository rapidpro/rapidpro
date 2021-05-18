from django.db import models
from django.utils import timezone

from temba.contacts.models import Contact
from temba.orgs.models import Org


class AirtimeTransfer(models.Model):
    STATUS_SUCCESS = "S"
    STATUS_FAILED = "F"
    STATUS_CHOICES = ((STATUS_SUCCESS, "Success"), (STATUS_FAILED, "Failed"))

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="airtime_transfers")

    status = models.CharField(max_length=1, choices=STATUS_CHOICES)

    # the contact this transfer was to
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="airtime_transfers")

    # URN that received the transfer
    recipient = models.CharField(max_length=64)

    # URN that sent the transfer
    sender = models.CharField(max_length=64, null=True)

    currency = models.CharField(max_length=32, null=True)

    desired_amount = models.DecimalField(max_digits=10, decimal_places=2)

    actual_amount = models.DecimalField(max_digits=10, decimal_places=2)

    # when this was created
    created_on = models.DateTimeField(default=timezone.now)
