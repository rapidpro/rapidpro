from smartmin.views import SmartFormView
from django import forms
from gettext import gettext as _
from temba.classifiers.views import BaseConnectView

class ConnectView(BaseConnectView):
    class Form(forms.Form):
        number = forms.CharField(help_text=_("Your enterprise WhatsApp number"))

    form_class = Form
