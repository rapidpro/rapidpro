from __future__ import unicode_literals

from django.conf.urls import patterns, url
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_protect
from rest_framework.urlpatterns import format_suffix_patterns
from .channels import StripeHandler, TwilioHandler, VerboiceHandler, AfricasTalkingHandler, ZenviaHandler, M3TechHandler
from .channels import ExternalHandler, ShaqodoonHandler, NexmoHandler, InfobipHandler, Hub9Handler, VumiHandler
from .channels import KannelHandler, ClickatellHandler, PlivoHandler, HighConnectionHandler, BlackmynaHandler
from .channels import SMSCentralHandler, MageHandler, YoHandler
from .views import WebHookEventListView, WebHookEventReadView, WebHookView, WebHookSimulatorView, WebHookTunnelView
from .views import api, ApiExplorerView, AuthenticateEndpoint, OrgEndpoint
from .views import BroadcastEndpoint, MessageEndpoint, MessageBulkActionEndpoint, LabelEndpoint
from .views import CallEndpoint, ContactEndpoint, ContactBulkActionEndpoint
from .views import FlowEndpoint, FlowResultsEndpoint, FlowRunEndpoint, FlowDefinitionEndpoint, FlowStepEndpoint
from .views import GroupEndpoint, FieldEndpoint
from .views import ChannelEndpoint, CampaignEndpoint, CampaignEventEndpoint, BoundaryEndpoint, AssetEndpoint

urlpatterns = patterns('api.views',
                       url(r'^$', api, name='api'),
                       url(r'^/stripe/$', StripeHandler.as_view(), name='api.stripe_handler'),
                       url(r'^/twilio/$', TwilioHandler.as_view(), name='api.twilio_handler'),
                       url(r'^/verboice/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$', VerboiceHandler.as_view(), name='api.verboice_handler'),
                       url(r'^/africastalking/(?P<action>delivery|callback)/(?P<uuid>[a-z0-9\-]+)/$', AfricasTalkingHandler.as_view(), name='api.africas_talking_handler'),
                       url(r'^/zenvia/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/$', ZenviaHandler.as_view(), name='api.zenvia_handler'),
                       url(r'^/external/(?P<action>sent|delivered|failed|received)/(?P<uuid>[a-z0-9\-]+)/$', ExternalHandler.as_view(), name='api.external_handler'),
                       url(r'^/shaqodoon/(?P<action>sent|delivered|failed|received)/(?P<uuid>[a-z0-9\-]+)/$', ShaqodoonHandler.as_view(), name='api.shaqodoon_handler'),
                       url(r'^/nexmo/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/$', NexmoHandler.as_view(), name='api.nexmo_handler'),
                       url(r'^/infobip/(?P<action>sent|delivered|failed|received)/(?P<uuid>[a-z0-9\-]+)/?$', InfobipHandler.as_view(), name='api.infobip_handler'),
                       url(r'^/hub9/(?P<action>sent|delivered|failed|received)/(?P<uuid>[a-z0-9\-]+)/?$', Hub9Handler.as_view(), name='api.hub9_handler'),
                       url(r'^/vumi/(?P<action>event|receive)/(?P<uuid>[a-z0-9\-]+)/?$', VumiHandler.as_view(), name='api.vumi_handler'),
                       url(r'^/kannel/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$', KannelHandler.as_view(), name='api.kannel_handler'),
                       url(r'^/clickatell/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$', ClickatellHandler.as_view(), name='api.clickatell_handler'),
                       url(r'^/plivo/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$', PlivoHandler.as_view(), name='api.plivo_handler'),
                       url(r'^/hcnx/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$', HighConnectionHandler.as_view(), name='api.hcnx_handler'),
                       url(r'^/blackmyna/(?P<action>status|receive)/(?P<uuid>[a-z0-9\-]+)/?$', BlackmynaHandler.as_view(), name='api.blackmyna_handler'),
                       url(r'^/smscentral/(?P<action>receive)/(?P<uuid>[a-z0-9\-]+)/?$', SMSCentralHandler.as_view(), name='api.smscentral_handler'),
                       url(r'^/m3tech/(?P<action>sent|delivered|failed|received)/(?P<uuid>[a-z0-9\-]+)/?$', M3TechHandler.as_view(), name='api.m3tech_handler'),
                       url(r'^/yo/(?P<action>received)/(?P<uuid>[a-z0-9\-]+)/?$', YoHandler.as_view(), name='api.yo_handler'),

                       url(r'^/mage/(?P<action>handle_message|follow_notification)$', MageHandler.as_view(), name='api.mage_handler'),

                       url(r'^/log/$', WebHookEventListView.as_view(), name='api.log'),
                       url(r'^/log/(?P<pk>\d+)/$', WebHookEventReadView.as_view(), name='api.log_read'),

                       url(r'^/explorer/$', ApiExplorerView.as_view(), name='api.explorer'),

                       url(r'^/webhook/$', WebHookView.as_view(), name='api.webhook'),
                       url(r'^/webhook/simulator/$', WebHookSimulatorView.as_view(), name='api.webhook_simulator'),
                       url(r'^/webhook/tunnel/$', login_required(csrf_protect(WebHookTunnelView.as_view())), name='api.webhook_tunnel'),

                       url(r'^/authenticate$', AuthenticateEndpoint.as_view(), name='api.authenticate'),
                       url(r'^/broadcasts$', BroadcastEndpoint.as_view(), name='api.broadcasts'),
                       url(r'^/messages$', MessageEndpoint.as_view(), name='api.messages'),
                       url(r'^/message_actions$', MessageBulkActionEndpoint.as_view(), name='api.message_actions'),
                       url(r'^/sms$', MessageEndpoint.as_view(), name='api.sms'),  # deprecated
                       url(r'^/labels$', LabelEndpoint.as_view(), name='api.labels'),
                       url(r'^/flows$', FlowEndpoint.as_view(), name='api.flows'),
                       url(r'^/flow_definition$', FlowDefinitionEndpoint.as_view(), name='api.flow_definition'),
                       url(r'^/results$', FlowResultsEndpoint.as_view(), name='api.results'),
                       url(r'^/runs$', FlowRunEndpoint.as_view(), name='api.runs'),
                       url(r'^/steps$', FlowStepEndpoint.as_view(), name='api.steps'),
                       url(r'^/calls$', CallEndpoint.as_view(), name='api.calls'),
                       url(r'^/contacts$', ContactEndpoint.as_view(), name='api.contacts'),
                       url(r'^/contact_actions$', ContactBulkActionEndpoint.as_view(), name='api.contact_actions'),
                       url(r'^/groups$', GroupEndpoint.as_view(), name='api.contactgroups'),
                       url(r'^/fields$', FieldEndpoint.as_view(), name='api.contactfields'),
                       url(r'^/relayers$', ChannelEndpoint.as_view(), name='api.channels'),
                       url(r'^/campaigns$', CampaignEndpoint.as_view(), name='api.campaigns'),
                       url(r'^/events$', CampaignEventEndpoint.as_view(), name='api.campaignevents'),
                       url(r'^/boundaries$', BoundaryEndpoint.as_view(), name='api.boundaries'),
                       url(r'^/org$', OrgEndpoint.as_view(), name='api.org'),
                       url(r'^/assets$', AssetEndpoint.as_view(), name='api.assets'))

# Format suffixes
urlpatterns = format_suffix_patterns(urlpatterns, allowed=['json', 'xml', 'api'])
