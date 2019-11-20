from uuid import uuid4

from smartmin.models import SmartModel

from django.db import models
from django.utils.translation import ugettext_lazy as _

from temba.orgs.models import Org


class Global(SmartModel):
    """
    A global is a constant value that can be used in templates in flows and messages.
    """

    MAX_KEY_LEN = 36
    MAX_NAME_LEN = 36
    MAX_VALUE_LEN = 640

    uuid = models.UUIDField(default=uuid4)

    org = models.ForeignKey(Org, related_name="globals", on_delete=models.PROTECT)

    key = models.CharField(verbose_name=_("Key"), max_length=MAX_KEY_LEN)

    name = models.CharField(verbose_name=_("Name"), max_length=MAX_NAME_LEN)

    value = models.TextField(max_length=MAX_VALUE_LEN)

    @classmethod
    def get_or_create(cls, org, user, key, name, value):
        return cls.objects.create(org=org, key=key, name=name, value=value, created_by=user, modified_by=user)

    def release(self):
        self.delete()

    def __str__(self):
        return f"global[key={self.key},name={self.name}]"
