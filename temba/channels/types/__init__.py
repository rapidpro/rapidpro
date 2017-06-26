from __future__ import unicode_literals

from ..models import Channel, ChannelType, SEND_FUNCTIONS
from .twitter.type import TwitterType
from .twitter_activity.type import TwitterActivityType

# TODO enumerate types dynamically
TYPE_CLASSES = [TwitterType, TwitterActivityType]


TYPES = {}
for type_class in TYPE_CLASSES:
    if type_class.code in TYPES:  # pragma: no cover
        raise ValueError("More than channel type with code: %s" % type_class.code)
    TYPES[type_class.code] = type_class()


# create types on the fly for each type not yet converted to a dynamic type
for code, name in Channel.TYPE_CHOICES:
    type_settings = Channel.CHANNEL_SETTINGS[code]
    type_class = type(str(code + 'Type'), (ChannelType,), dict(
        code=code,
        name=name,
        icon=Channel.TYPE_ICONS.get(code, 'icon-channel-external'),
        show_config_page=code not in Channel.HIDE_CONFIG_PAGE,
        scheme=type_settings.get('scheme'),
        max_length=type_settings.get('max_length'),
        max_tps=type_settings.get('max_tps'),
        attachment_support=code in Channel.MEDIA_CHANNELS,
        send=SEND_FUNCTIONS.get(code)
    ))
    TYPES[code] = type_class()
