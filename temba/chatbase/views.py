from __future__ import unicode_literals

from django.utils.translation import ugettext_lazy as _
from smartmin.views import SmartListView, SmartReadView, SmartCRUDL
from temba.chatbase.models import Chatbase
from temba.orgs.views import OrgPermsMixin, OrgObjPermsMixin


class ChatbaseCRUDL(SmartCRUDL):
    model = Chatbase
    actions = ('list', 'read')

    class List(OrgPermsMixin, SmartListView):
        fields = ('status', 'message', 'channel', 'contact', 'created_on')
        title = _("Recent Chatbase Events")
        default_order = ('-created_on',)
        field_config = dict(created_on=dict(label="Time"))
        link_fields = ('message',)

        def get_status(self, obj):
            return obj.get_status_display()

        def derive_queryset(self, **kwargs):
            org = self.derive_org()
            return Chatbase.objects.filter(org=org)

        def get_channel(self, obj):  # pragma: needs cover
            if obj.channel:
                return obj.channel
            return "--"

        def get_context_data(self, **kwargs):
            context = super(ChatbaseCRUDL.List, self).get_context_data(**kwargs)
            context['org'] = self.derive_org()
            return context

    class Read(OrgObjPermsMixin, SmartReadView):
        title = _("Chatbase Event Details")
        field_config = dict(created_on=dict(label="Time"))

        def get_context_data(self, **kwargs):
            context = super(ChatbaseCRUDL.Read, self).get_context_data(**kwargs)
            context['show_logs'] = self.show_logs()
            return context

        def show_logs(self):
            org = self.derive_org()
            user = self.request.user
            if not org.is_anon or user.is_superuser or user.is_staff:
                return True
            return False  # pragma: needs cover

        def derive_fields(self):
            if self.show_logs():
                return 'contact', 'status', 'channel', 'message', 'created_on'

            return 'status', 'channel', 'message', 'created_on'  # pragma: needs cover

        def get_status(self, obj):
            return obj.get_status_display()

        def derive_queryset(self, **kwargs):
            org = self.derive_org()
            return Chatbase.objects.filter(org=org)

        def get_channel(self, obj):
            if obj.channel:
                return obj.channel  # pragma: needs cover
            return "--"
