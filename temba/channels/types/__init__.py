# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from collections import OrderedDict
from django.conf import settings
from django.utils.module_loading import import_string

from temba.channels.views import TYPE_UPDATE_FORM_CLASSES
from ..models import Channel, ChannelType, SEND_FUNCTIONS

TYPES = OrderedDict({})


def register_channel_type(type_class):
    """
    Registers a channel type
    """
    if not type_class.slug:
        type_class.slug = type_class.__module__.split('.')[-2]

    if type_class.code in TYPES:  # pragma: no cover
        raise ValueError("More than channel type with code: %s" % type_class.code)
    TYPES[type_class.code] = type_class()


def reload_channel_types():
    """
    Re-loads the dynamic channel types
    """
    for class_name in settings.CHANNEL_TYPES:
        register_channel_type(import_string(class_name))

    # create types on the fly for each type not yet converted to a dynamic type
    for code, name in Channel.TYPE_CHOICES:
        type_settings = Channel.CHANNEL_SETTINGS[code]
        dyn_type_class = type(str(code + 'Type'), (ChannelType,), dict(
            code=code,
            name=name,
            slug=code.lower(),
            icon=Channel.TYPE_ICONS.get(code, 'icon-channel-external'),
            show_config_page=code not in Channel.HIDE_CONFIG_PAGE,
            schemes=type_settings.get('schemes'),
            max_length=type_settings.get('max_length'),
            max_tps=type_settings.get('max_tps'),
            attachment_support=False,
            free_sending=False,
            update_form=TYPE_UPDATE_FORM_CLASSES.get(code),
            send=SEND_FUNCTIONS.get(code),
            ivr_protocol=None
        ))
        register_channel_type(dyn_type_class)


reload_channel_types()
