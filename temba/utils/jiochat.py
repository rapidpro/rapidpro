from temba.utils.access_token import APIClient


class JiochatClient(APIClient):
    API_NAME = "JioChat"
    API_SLUG = "jiochat"
    TOKEN_URL = "https://channels.jiochat.com/auth/token.action"
    TOKEN_REFRESH_LOCK = "jiochat_channel_access_token:refresh-lock:%s"
    TOKEN_STORE_KEY = "jiochat_channel_access_token:%s"
