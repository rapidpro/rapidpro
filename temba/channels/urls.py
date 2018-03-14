# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.conf.urls import url, include
from .handlers import VerboiceHandler, AfricasTalkingHandler, ZenviaHandler, M3TechHandler
from .handlers import ExternalHandler, ShaqodoonHandler, NexmoHandler, InfobipHandler, Hub9Handler
from .handlers import KannelHandler, ClickatellHandler, PlivoHandler, HighConnectionHandler, BlackmynaHandler
from .handlers import SMSCentralHandler, MageHandler, YoHandler, get_channel_handlers
from .models import Channel
from .views import ChannelCRUDL, ChannelEventCRUDL, ChannelLogCRUDL

courier_urls = []
handler_urls = []

for handler in get_channel_handlers():
    rel_url, url_name = handler.get_courier_url()
    if rel_url:
        courier_urls.append(url(rel_url, handler.as_view(), name=url_name))

    rel_url, url_name = handler.get_handler_url()
    if rel_url:
        handler_urls.append(url(rel_url, handler.as_view(), name=url_name))

# we iterate all our channel types, finding all the URLs they want to wire in
type_urls = []
for ch_type in Channel.get_types():
    channel_urls = ch_type.get_urls()
    for u in channel_urls:
        u.name = 'channels.types.%s.%s' % (ch_type.slug, u.name)

    if channel_urls:
        type_urls.append(
            url('^%s/' % ch_type.slug, include(channel_urls))
        )

urlpatterns = [
    url(r'^', include(ChannelEventCRUDL().as_urlpatterns())),
    url(r'^channels/', include(ChannelCRUDL().as_urlpatterns() + ChannelLogCRUDL().as_urlpatterns())),

    url(r'^c/', include(courier_urls)),
    url(r'^handlers/', include(handler_urls)),
    url(r'^channels/types/', include(type_urls)),

    # for backwards compatibility these channel handlers are exposed at /api/v1 as well
    url(r'^api/v1/', include([
        url(r'^verboice/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$', VerboiceHandler.as_view()),
        url(r'^africastalking/(?P<action>delivery|callback)/(?P<uuid>[a-z0-9\-]+)/$', AfricasTalkingHandler.as_view()),
        url(r'^zenvia/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/$', ZenviaHandler.as_view()),
        url(r'^external/(?P<action>sent|delivered|failed|received)/(?P<uuid>[a-z0-9\-]+)/$', ExternalHandler.as_view()),
        url(r'^shaqodoon/(?P<action>sent|delivered|failed|received)/(?P<uuid>[a-z0-9\-]+)/$', ShaqodoonHandler.as_view()),
        url(r'^nexmo/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/$', NexmoHandler.as_view()),
        url(r'^infobip/(?P<action>sent|delivered|failed|received)/(?P<uuid>[a-z0-9\-]+)/?$', InfobipHandler.as_view()),
        url(r'^hub9/(?P<action>sent|delivered|failed|received)/(?P<uuid>[a-z0-9\-]+)/?$', Hub9Handler.as_view()),
        url(r'^kannel/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$', KannelHandler.as_view()),
        url(r'^clickatell/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$', ClickatellHandler.as_view()),
        url(r'^plivo/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$', PlivoHandler.as_view()),
        url(r'^hcnx/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$', HighConnectionHandler.as_view()),
        url(r'^blackmyna/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$', BlackmynaHandler.as_view()),
        url(r'^smscentral/(?P<action>receive)/(?P<uuid>[a-z0-9\-]+)/?$', SMSCentralHandler.as_view()),
        url(r'^m3tech/(?P<action>sent|delivered|failed|received)/(?P<uuid>[a-z0-9\-]+)/?$', M3TechHandler.as_view()),
        url(r'^yo/(?P<action>received)/(?P<uuid>[a-z0-9\-]+)/?$', YoHandler.as_view()),
        url(r'^mage/(?P<action>handle_message|follow_notification|stop_contact)$', MageHandler.as_view())
    ]))
]
