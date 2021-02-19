from django.db import models
from .managers import MessagesReportManager


class MessagesDailyCount(models.Model):
    objects = MessagesReportManager
