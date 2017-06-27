from __future__ import unicode_literals, absolute_import

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import ugettext_lazy as _
from smartmin.mixins import PassRequestToFormMixin
from smartmin.views import SmartFormView
from temba.utils.twitter import TembaTwython, TwythonError
from ...models import Channel
from ...views import ClaimView


class ClaimTwitterActivity(ClaimView, PassRequestToFormMixin, SmartFormView):
    class Form(forms.Form):
        api_key = forms.CharField(label=_('Consumer Key'))
        api_secret = forms.CharField(label=_('Consumer Secret'))
        access_token = forms.CharField(label=_('Access Token'))
        access_token_secret = forms.CharField(label=_('Access Token Secret'))

        def __init__(self, **kwargs):
            self.org = kwargs.pop('request').user.get_org()
            super(ClaimTwitterActivity.Form, self).__init__(**kwargs)

        def clean(self):
            cleaned_data = super(ClaimTwitterActivity.Form, self).clean()
            api_key = cleaned_data.get('api_key')
            api_secret = cleaned_data.get('api_secret')
            access_token = cleaned_data.get('access_token')
            access_token_secret = cleaned_data.get('access_token_secret')

            if api_key and api_secret and access_token and access_token_secret:
                twitter = TembaTwython(api_key, api_secret, access_token, access_token_secret)
                try:
                    user = twitter.verify_credentials()

                    # check there isn't already a channel for this Twitter account
                    if self.org.channels.filter(channel_type='TWT', address=user['screen_name'],
                                                is_active=True).exists():
                        raise ValidationError(_("A Twitter channel already exists for that handle."))

                except TwythonError:
                    raise ValidationError(_("The provided Twitter credentials do not appear to be valid."))

            return cleaned_data

    form_class = Form

    def form_valid(self, form):
        org = self.request.user.get_org()

        cleaned_data = form.cleaned_data
        api_key = cleaned_data['api_key']
        api_secret = cleaned_data['api_secret']
        access_token = cleaned_data['access_token']
        access_token_secret = cleaned_data['access_token_secret']

        self.object = Channel.add_twitter_activity_channel(org, self.request.user, api_key, api_secret, access_token,
                                                           access_token_secret)

        return super(ClaimTwitterActivity, self).form_valid(form)
