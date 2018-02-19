# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.db import models
from django.utils.translation import ugettext_lazy as _
from smartmin.models import SmartModel


class Lead(SmartModel):
    email = models.EmailField(unique=False,
                              error_messages={'unique': '{% trans "This email has already been registered." %}'})


class Video(SmartModel):
    name = models.CharField(verbose_name=_("Name"),
                            help_text=_("The name of the video"), max_length=255)
    summary = models.TextField(verbose_name=_("Summary"),
                               help_text=_("A short blurb about the video"))
    description = models.TextField(verbose_name=_("Description"),
                                   help_text="The full description for the video")
    vimeo_id = models.CharField(verbose_name=_("Vimeo ID"), max_length=255,
                                help_text=_("The id vimeo uses for this video"))
    order = models.IntegerField(default=0)
