from __future__ import unicode_literals

from rest_framework import exceptions
from rest_framework.authentication import TokenAuthentication
from .models import APIToken


class APITokenAuthentication(TokenAuthentication):
    """
    Simple token based authentication.

    Clients should authenticate by passing the token key in the "Authorization"
    HTTP header, prepended with the string "Token ".  For example:

        Authorization: Token 401f7ac837da42b97f613d789819ff93537bee6a
    """
    model = APIToken

    def authenticate_credentials(self, key):
        try:
            token = self.model.objects.get(key=key)
        except self.model.DoesNotExist:
            raise exceptions.AuthenticationFailed('Invalid token')

        if token.user.is_active:
            # set the org on this user
            token.user.set_org(token.org)

            return token.user, token

        raise exceptions.AuthenticationFailed('User inactive or deleted')
