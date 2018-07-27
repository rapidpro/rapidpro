# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import six

from django.conf import settings
from django.db import models
from django.utils.translation import ugettext_lazy as _
from temba.locations.models import AdminBoundary
from temba.orgs.models import Org


@six.python_2_unicode_compatible
class Value(models.Model):
    """
    A value holds a contact specific value for a contact field.
    """
    TYPE_TEXT = 'T'
    TYPE_DECIMAL = 'N'
    TYPE_DATETIME = 'D'
    TYPE_STATE = 'S'
    TYPE_DISTRICT = 'I'
    TYPE_WARD = 'W'

    TYPE_CONFIG = ((TYPE_TEXT, _("Text"), 'text'),
                   (TYPE_DECIMAL, _("Numeric"), 'numeric'),
                   (TYPE_DATETIME, _("Date & Time"), 'datetime'),
                   (TYPE_STATE, _("State"), 'state'),
                   (TYPE_DISTRICT, _("District"), 'district'),
                   (TYPE_WARD, _("Ward"), 'ward'))

    TYPE_CHOICES = [(c[0], c[1]) for c in TYPE_CONFIG]

    GPS = 'G'
    AUDIO = 'A'
    VIDEO = 'V'
    IMAGE = 'I'

    MEDIA_TYPES = ((GPS, _("GPS Coordinates")),
                   (VIDEO, _("Video")),
                   (AUDIO, _("Audio")),
                   (IMAGE, _("Image")))

    MAX_VALUE_LEN = settings.VALUE_FIELD_SIZE

    contact = models.ForeignKey('contacts.Contact', related_name='values')

    contact_field = models.ForeignKey('contacts.ContactField', null=True, on_delete=models.SET_NULL,
                                      help_text="The ContactField this value is for, if any")

    ruleset = models.ForeignKey('flows.RuleSet', null=True, on_delete=models.SET_NULL,
                                help_text="The RuleSet this value is for, if any")

    run = models.ForeignKey('flows.FlowRun', null=True, related_name='values', on_delete=models.SET_NULL,
                            help_text="The FlowRun this value is for, if any")

    rule_uuid = models.CharField(max_length=255, null=True, db_index=True,
                                 help_text="The rule that matched, only appropriate for RuleSet values")

    category = models.CharField(max_length=128, null=True,
                                help_text="The name of the category this value matched in the RuleSet")

    string_value = models.TextField(help_text="The string value or string representation of this value")

    decimal_value = models.DecimalField(max_digits=36, decimal_places=8, null=True,
                                        help_text="The decimal value of this value if any.")
    datetime_value = models.DateTimeField(null=True,
                                          help_text="The datetime value of this value if any.")

    location_value = models.ForeignKey(AdminBoundary, on_delete=models.SET_NULL, null=True,
                                       help_text="The location value of this value if any.")

    media_value = models.TextField(max_length=640, null=True, help_text="The media value if any.")

    org = models.ForeignKey(Org)

    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)

    def __str__(self):  # pragma: needs cover
        if self.ruleset:
            return "Contact: %d - %s = %s" % (self.contact.pk, self.ruleset.label, self.category)
        elif self.contact_field:
            return "Contact: %d - %s = %s" % (self.contact.pk, self.contact_field.label, self.string_value)
        else:
            return "Contact: %d - %s" % (self.contact.pk, self.string_value)
