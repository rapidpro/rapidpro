# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.contrib.auth.models import User
from temba.tests import TembaTest


class UserTest(TembaTest):
    def test_user_model(self):
        long_username = 'bob12345678901234567890123456789012345678901234567890@msn.com'
        User.objects.create(username=long_username, email=long_username)
