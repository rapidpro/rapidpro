from temba.channels.models import ChannelConnection


class USSDSession(ChannelConnection):
    class Meta:
        proxy = True
