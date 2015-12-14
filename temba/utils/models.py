from __future__ import unicode_literals

from django.db import models
from django.utils.translation import ugettext_lazy as _
from smartmin.models import SmartModel
from uuid import uuid4


def generate_uuid():
    return unicode(uuid4())


class TembaModel(SmartModel):

    uuid = models.CharField(max_length=36, unique=True, db_index=True, default=generate_uuid,
                            verbose_name=_("Unique Identifier"), help_text=_("The unique identifier for this object"))

    class Meta:
        abstract = True
