# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.utils.translation import ugettext_lazy as _
from smartmin.views import SmartListView, SmartReadView, SmartCRUDL
from temba.airtime.models import AirtimeTransfer
from temba.orgs.views import OrgPermsMixin, OrgObjPermsMixin


class AirtimeCRUDL(SmartCRUDL):
    model = AirtimeTransfer
    actions = ('list', 'read')

    class List(OrgPermsMixin, SmartListView):
        fields = ('status', 'message', 'amount', 'contact', 'created_on')
        title = _("Recent Airtime Transfers")
        default_order = ('-created_on',)
        field_config = dict(created_on=dict(label="Time"))
        link_fields = ('message',)

        def get_status(self, obj):
            return obj.get_status_display()

        def derive_queryset(self, **kwargs):
            org = self.derive_org()
            return AirtimeTransfer.objects.filter(org=org)

        def get_channel(self, obj):  # pragma: needs cover
            if obj.channel:
                return obj.channel
            return "--"

        def get_context_data(self, **kwargs):
            context = super(AirtimeCRUDL.List, self).get_context_data(**kwargs)
            context['org'] = self.derive_org()
            return context

    class Read(OrgObjPermsMixin, SmartReadView):
        title = _("Airtime Transfer Details")
        field_config = dict(created_on=dict(label="Time"))

        def get_context_data(self, **kwargs):
            context = super(AirtimeCRUDL.Read, self).get_context_data(**kwargs)
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
                return ('contact', 'status', 'channel', 'amount', 'message',
                        'recipient', 'denomination', 'created_on')

            return ('contact', 'status', 'channel', 'amount', 'message', 'created_on')  # pragma: needs cover

        def get_status(self, obj):
            return obj.get_status_display()

        def derive_queryset(self, **kwargs):
            org = self.derive_org()
            return AirtimeTransfer.objects.filter(org=org)

        def get_channel(self, obj):
            if obj.channel:
                return obj.channel  # pragma: needs cover
            return "--"
