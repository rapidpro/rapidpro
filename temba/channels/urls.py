from django.conf.urls import include, url

from .handlers import get_channel_handlers
from .models import Channel
from .views import ChannelCRUDL, ChannelEventCRUDL, ChannelLogCRUDL

courier_urls = []
handler_urls = []

for handler in get_channel_handlers():
    rel_url, url_name = handler.get_handler_url()
    if rel_url:
        handler_urls.append(url(rel_url, handler.as_view(), name=url_name))

# we iterate all our channel types, finding all the URLs they want to wire in
type_urls = []
for ch_type in Channel.get_types():
    channel_urls = ch_type.get_urls()
    for u in channel_urls:
        u.name = "channels.types.%s.%s" % (ch_type.slug, u.name)

    if channel_urls:
        type_urls.append(url("^%s/" % ch_type.slug, include(channel_urls)))

    courier_url = ch_type.get_courier_url()
    if courier_url:
        courier_urls.append(courier_url)


urlpatterns = [
    url(r"^", include(ChannelEventCRUDL().as_urlpatterns())),
    url(r"^channels/", include(ChannelCRUDL().as_urlpatterns() + ChannelLogCRUDL().as_urlpatterns())),
    url(r"^c/", include(courier_urls)),
    url(r"^handlers/", include(handler_urls)),
    url(r"^channels/types/", include(type_urls)),
]
