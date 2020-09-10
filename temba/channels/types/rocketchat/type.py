from ...models import ChannelType


class RocketChatType(ChannelType):
    code = "RC"
    category = ChannelType.Category.SOCIAL_MEDIA