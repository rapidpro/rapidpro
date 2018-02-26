# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _
from smartmin.views import SmartTemplateView
from temba.utils.twitter import TembaTwython
from ...models import Channel
from ...views import ClaimViewMixin


SESSION_TWITTER_API_KEY = 'twitter_api_key'
SESSION_TWITTER_API_SECRET = 'twitter_api_secret'
SESSION_TWITTER_OAUTH_TOKEN = 'twitter_oauth_token'
SESSION_TWITTER_OAUTH_SECRET = 'twitter_oauth_token_secret'


class ClaimView(ClaimViewMixin, SmartTemplateView):

    def pre_process(self, *args, **kwargs):
        response = super(ClaimView, self).pre_process(*args, **kwargs)

        api_key = settings.TWITTER_API_KEY
        api_secret = settings.TWITTER_API_SECRET
        oauth_token = self.request.session.get(SESSION_TWITTER_OAUTH_TOKEN)
        oauth_token_secret = self.request.session.get(SESSION_TWITTER_OAUTH_SECRET)
        oauth_verifier = self.request.GET.get('oauth_verifier')

        # if we have all required values, then we must be returning from an authorization callback
        if api_key and api_secret and oauth_token and oauth_token_secret and oauth_verifier:
            twitter = TembaTwython(api_key, api_secret, oauth_token, oauth_token_secret)
            final_step = twitter.get_authorized_tokens(oauth_verifier)
            screen_name = final_step['screen_name']
            handle_id = final_step['user_id']
            oauth_token = final_step['oauth_token']
            oauth_token_secret = final_step['oauth_token_secret']

            org = self.request.user.get_org()

            del self.request.session[SESSION_TWITTER_OAUTH_TOKEN]
            del self.request.session[SESSION_TWITTER_OAUTH_SECRET]

            # check there isn't already a channel for this Twitter account
            if self.org.channels.filter(channel_type=self.channel_type.code, address=screen_name, is_active=True).exists():
                messages.error(self.request, _("A Twitter channel for that handle already exists, and must be removed"
                                               "before another channel can be created for that handle."))
                return response
            else:
                config = {
                    'handle_id': int(handle_id),
                    'oauth_token': oauth_token,
                    'oauth_token_secret': oauth_token_secret
                }
                self.object = Channel.create(org, self.request.user, None, self.channel_type,
                                             name="@%s" % screen_name, address=screen_name, config=config)

            return redirect(self.get_success_url())

        return response

    def get_context_data(self, **kwargs):
        context = super(ClaimView, self).get_context_data(**kwargs)

        # generate temp OAuth token and secret
        twitter = TembaTwython(settings.TWITTER_API_KEY, settings.TWITTER_API_SECRET)
        callback_url = self.request.build_absolute_uri(reverse('channels.types.twitter.claim'))
        auth = twitter.get_authentication_tokens(callback_url=callback_url)

        # put in session for when we return from callback
        self.request.session[SESSION_TWITTER_OAUTH_TOKEN] = auth['oauth_token']
        self.request.session[SESSION_TWITTER_OAUTH_SECRET] = auth['oauth_token_secret']

        context['twitter_auth_url'] = auth['auth_url']
        return context
