from __future__ import unicode_literals

from ..models import Channel, ChannelType, SEND_FUNCTIONS
from .twitter.type import TwitterType

# TODO enumerate types dynamically
TYPES = {
    TwitterType.code: TwitterType()
}

# create types on the fly for each type not yet converted to a dynamic type
for code, name in Channel.TYPE_CHOICES:
    type_settings = Channel.CHANNEL_SETTINGS[code]
    type_class = type(str(code + 'Type'), (ChannelType,), dict(
        name=name,
        code=code,
        scheme=type_settings.get('scheme'),
        max_length=type_settings.get('max_length'),
        max_tps=type_settings.get('max_tps'),
        show_config_page=code not in Channel.HIDE_CONFIG_PAGE,
        attachment_support=code in Channel.MEDIA_CHANNELS,
        send=SEND_FUNCTIONS.get(code)
    ))
    TYPES[code] = type_class()
