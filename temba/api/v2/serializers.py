from __future__ import absolute_import, unicode_literals

from rest_framework import serializers


class ReadSerializer(serializers.ModelSerializer):
    """
    We deviate slightly from regular REST framework usage with distinct serializers for reading and writing
    """
    pass

# TODO add serializers
