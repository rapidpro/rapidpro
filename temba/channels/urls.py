from __future__ import absolute_import, unicode_literals

from django.conf.urls import url, include
from .handlers import TwilioHandler, VerboiceHandler, AfricasTalkingHandler, ZenviaHandler, M3TechHandler
from .handlers import ExternalHandler, ShaqodoonHandler, NexmoHandler, InfobipHandler, Hub9Handler, VumiHandler
from .handlers import KannelHandler, ClickatellHandler, PlivoHandler, HighConnectionHandler, BlackmynaHandler
from .handlers import SMSCentralHandler, MageHandler, YoHandler, StartHandler
from .views import ChannelCRUDL, ChannelLogCRUDL


urlpatterns = [
    url(r'^channels/', include(ChannelCRUDL().as_urlpatterns() + ChannelLogCRUDL().as_urlpatterns())),

    url(r'^handlers', include([
        url(r'^/twilio/$', TwilioHandler.as_view(), name='handlers.twilio_handler'),
        url(r'^/verboice/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$', VerboiceHandler.as_view(), name='handlers.verboice_handler'),
        url(r'^/africastalking/(?P<action>delivery|callback)/(?P<uuid>[a-z0-9\-]+)/$', AfricasTalkingHandler.as_view(), name='handlers.africas_talking_handler'),
        url(r'^/zenvia/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/$', ZenviaHandler.as_view(), name='handlers.zenvia_handler'),
        url(r'^/external/(?P<action>sent|delivered|failed|received)/(?P<uuid>[a-z0-9\-]+)/$', ExternalHandler.as_view(), name='handlers.external_handler'),
        url(r'^/shaqodoon/(?P<action>sent|delivered|failed|received)/(?P<uuid>[a-z0-9\-]+)/$', ShaqodoonHandler.as_view(), name='handlers.shaqodoon_handler'),
        url(r'^/nexmo/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/$', NexmoHandler.as_view(), name='handlers.nexmo_handler'),
        url(r'^/infobip/(?P<action>sent|delivered|failed|received)/(?P<uuid>[a-z0-9\-]+)/?$', InfobipHandler.as_view(), name='handlers.infobip_handler'),
        url(r'^/hub9/(?P<action>sent|delivered|failed|received)/(?P<uuid>[a-z0-9\-]+)/?$', Hub9Handler.as_view(), name='handlers.hub9_handler'),
        url(r'^/vumi/(?P<action>event|receive)/(?P<uuid>[a-z0-9\-]+)/?$', VumiHandler.as_view(), name='handlers.vumi_handler'),
        url(r'^/kannel/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$', KannelHandler.as_view(), name='handlers.kannel_handler'),
        url(r'^/clickatell/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$', ClickatellHandler.as_view(), name='handlers.clickatell_handler'),
        url(r'^/plivo/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$', PlivoHandler.as_view(), name='handlers.plivo_handler'),
        url(r'^/hcnx/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$', HighConnectionHandler.as_view(), name='handlers.hcnx_handler'),
        url(r'^/blackmyna/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$', BlackmynaHandler.as_view(), name='handlers.blackmyna_handler'),
        url(r'^/smscentral/(?P<action>receive)/(?P<uuid>[a-z0-9\-]+)/?$', SMSCentralHandler.as_view(), name='handlers.smscentral_handler'),
        url(r'^/start/(?P<action>receive)/(?P<uuid>[a-z0-9\-]+)/?$', StartHandler.as_view(), name='handlers.start_handler'),
        url(r'^/m3tech/(?P<action>sent|delivered|failed|received)/(?P<uuid>[a-z0-9\-]+)/?$', M3TechHandler.as_view(), name='handlers.m3tech_handler'),
        url(r'^/yo/(?P<action>received)/(?P<uuid>[a-z0-9\-]+)/?$', YoHandler.as_view(), name='handlers.yo_handler'),
        url(r'^/mage/(?P<action>handle_message|follow_notification)$', MageHandler.as_view(), name='handlers.mage_handler')
    ])),

    # for backwards compatibility these channel handlers are exposed at /api/v1 as well
    url(r'^api/v1', include([
        url(r'^/twilio/$', TwilioHandler.as_view()),
        url(r'^/verboice/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$', VerboiceHandler.as_view()),
        url(r'^/africastalking/(?P<action>delivery|callback)/(?P<uuid>[a-z0-9\-]+)/$', AfricasTalkingHandler.as_view()),
        url(r'^/zenvia/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/$', ZenviaHandler.as_view()),
        url(r'^/external/(?P<action>sent|delivered|failed|received)/(?P<uuid>[a-z0-9\-]+)/$', ExternalHandler.as_view()),
        url(r'^/shaqodoon/(?P<action>sent|delivered|failed|received)/(?P<uuid>[a-z0-9\-]+)/$', ShaqodoonHandler.as_view()),
        url(r'^/nexmo/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/$', NexmoHandler.as_view()),
        url(r'^/infobip/(?P<action>sent|delivered|failed|received)/(?P<uuid>[a-z0-9\-]+)/?$', InfobipHandler.as_view()),
        url(r'^/hub9/(?P<action>sent|delivered|failed|received)/(?P<uuid>[a-z0-9\-]+)/?$', Hub9Handler.as_view()),
        url(r'^/vumi/(?P<action>event|receive)/(?P<uuid>[a-z0-9\-]+)/?$', VumiHandler.as_view()),
        url(r'^/kannel/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$', KannelHandler.as_view()),
        url(r'^/clickatell/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$', ClickatellHandler.as_view()),
        url(r'^/plivo/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$', PlivoHandler.as_view()),
        url(r'^/hcnx/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$', HighConnectionHandler.as_view()),
        url(r'^/blackmyna/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$', BlackmynaHandler.as_view()),
        url(r'^/smscentral/(?P<action>receive)/(?P<uuid>[a-z0-9\-]+)/?$', SMSCentralHandler.as_view()),
        url(r'^/m3tech/(?P<action>sent|delivered|failed|received)/(?P<uuid>[a-z0-9\-]+)/?$', M3TechHandler.as_view()),
        url(r'^/yo/(?P<action>received)/(?P<uuid>[a-z0-9\-]+)/?$', YoHandler.as_view()),
        url(r'^/mage/(?P<action>handle_message|follow_notification)$', MageHandler.as_view())
    ]))
]
