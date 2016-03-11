from __future__ import unicode_literals

import json

from django.db import models
from django.utils.translation import ugettext_lazy as _
from smartmin.models import SmartModel
from temba.orgs.models import Org


class Report(SmartModel):
    TITLE = 'title'
    DESCRIPTION = 'description'
    CONFIG = 'config'
    ID = 'id'

    title = models.CharField(verbose_name=_("Title"),
                             max_length=64,
                             help_text=_("The name title or this report"))

    description = models.TextField(verbose_name=_("Description"),
                                   help_text=_("The full description for the report"))

    org = models.ForeignKey(Org)

    config = models.TextField(null=True, verbose_name=_("Configuration"),
                              help_text=_("The JSON encoded configurations for this report"))

    is_published = models.BooleanField(default=False,
                                       help_text=_("Whether this report is currently published"))

    @classmethod
    def create_report(cls, org, user, json_dict):
        title = json_dict.get(Report.TITLE)
        description = json_dict.get(Report.DESCRIPTION)
        config = json_dict.get(Report.CONFIG)
        id = json_dict.get(Report.ID)

        existing = cls.objects.filter(pk=id, org=org)
        if existing:
            existing.update(title=title,
                            description=description,
                            config=json.dumps(config))

            return cls.objects.get(pk=id)

        return cls.objects.create(title=title,
                                  description=description,
                                  config=json.dumps(config),
                                  org=org,
                                  created_by=user,
                                  modified_by=user)

    def as_json(self):
        return dict(text=self.title, id=self.pk, description=self.description, config=self.config, public=self.is_published)

    def __unicode__(self):
        return "%s - %s" % (self.pk, self.title)

    class Meta:
        unique_together = (('org', 'title'),)
