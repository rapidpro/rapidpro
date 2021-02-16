from django.db import models
from .managers import MessagesReportManager
from ..utils.models import JSONField


class MessagesDailyCount(models.Model):
    org = models.ForeignKey(to="orgs.Org", on_delete=models.CASCADE)
    date = models.DateField(null=False)
    flow = models.ManyToManyField(to="flows.Flow", null=True)
    channel = models.ManyToManyField(to="channels.Channel", null=True)
    calculated_data = JSONField()
    objects = MessagesReportManager
