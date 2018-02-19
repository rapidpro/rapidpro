# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals


from django import forms
from django.utils.translation import ugettext_lazy as _
from smartmin.views import SmartFormView
from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        title = forms.CharField(label=_('Notification Title'))
        key = forms.CharField(label=_('FCM Key'),
                              help_text=_("The key provided on the the Firebase Console when you created your app."))
        send_notification = forms.CharField(label=_('Send notification'), required=False,
                                            help_text=_("Check if you want this channel to send notifications "
                                                        "to contacts."),
                                            widget=forms.CheckboxInput())

    form_class = Form

    def form_valid(self, form):
        org = self.request.user.get_org()
        title = form.cleaned_data.get('title')
        key = form.cleaned_data.get('key')
        config = {'FCM_TITLE': title, 'FCM_KEY': key}

        if form.cleaned_data.get('send_notification') == 'True':
            config['FCM_NOTIFICATION'] = True

        self.object = Channel.create(org, self.request.user, None, self.channel_type, name=title, address=key, config=config)

        return super(ClaimView, self).form_valid(form)
