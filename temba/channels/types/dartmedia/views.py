# -*- coding: utf-8 -*-
from temba.channels.views import AuthenticatedExternalClaimView
from temba.contacts.models import TEL_SCHEME, EXTERNAL_SCHEME


class ClaimView(AuthenticatedExternalClaimView):

    def get_country(self, obj):
        return "Indonesia"

    def get_submitted_country(self, data):
        return "ID"

    def get_channel_schemes(self, data):
        return [TEL_SCHEME, EXTERNAL_SCHEME]
