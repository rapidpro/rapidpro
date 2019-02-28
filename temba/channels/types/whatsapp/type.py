import requests

from django.conf.urls import url
from django.forms import ValidationError
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import Channel
from temba.channels.types.whatsapp.views import ClaimView, RefreshView
from temba.contacts.models import WHATSAPP_SCHEME

from ...models import ChannelType

# WhatsApp only supports some languages. For those they do support, we map from our ISO-639 codo to
# WhatsApp's iso639-2 / country pair. Note that not all combinations can be mapped, for example there
# are more WhatsApp variants of Spanish than are represented in ISO639-3. That's probably ok as
# individual orgs will hopefully not use all those variants at once.
LANGUAGE_MAPPING = dict(
    afr="af",  # Afrikans
    sqi="sq",  # Albanian
    ara="ar",  # Arabic
    aze="az",  # Azerbaijani
    ben="bn",  # Bengali
    bul="bg",  # Bulgarian
    cat="ca",  # Catalan
    zho="zh_CN",  # Chinese Macro Language
    yue="zh_HK",  # Cantonese Chinese (guess)
    cmn="zh_TW",  # Mandarin Chinese (guess)
    hrv="hr",  # Croatian
    ces="cs",  # Czech
    dah="da",  # Danish
    nld="nl",  # Dutch
    eng="en",  # English
    # en_GB (nothing from ISO-639-3)
    # en_US (nothing from ISO-639-3)
    est="et",  # Estonian
    fil="fil",  # Filipino
    fin="fi",  # Finnish
    fra="fr",  # French
    deu="de",  # German
    ell="el",  # Greek
    gul="gu",  # Gujarati
    enb="he",  # Hebrew
    hin="hi",  # Hindi
    hun="hu",  # Hungarian
    ind="id",  # Indonesian
    gle="ga",  # Irish
    ita="it",  # Italian
    jpn="ja",  # Japanese
    kan="kn",  # Kannada
    kaz="kk",  # Kazakh
    kor="ko",  # Korean
    lao="lo",  # Lao
    lav="lv",  # Latvian
    lit="lt",  # Lithuanian
    mkd="mk",  # Macedonian
    msa="ms",  # Malay
    mar="mr",  # Marathi
    nob="nb",  # Norwegian
    nor="nb",  # Norwegian
    fas="fa",  # Persian
    pol="pl",  # Polish
    # pt_BR (nothing from ISO-639-3)
    por="pt_PT",  # Portuguese
    pan="pa",  # Punjabi
    ron="ro",  # Romanian
    rus="ru",  # Russian
    srp="sr",  # Serbian
    slk="sk",  # Slovak
    slv="sl",  # Slovenian
    spa="es",  # Spanish
    # es_AR (nothing from ISO-639-3)
    # es_ES (nothing from ISO-639-3)
    # es_MX (nothing from ISO-639-3)
    swa="sw",  # Swahili
    swe="sv",  # Swedish
    tam="ta",  # Tamil
    tel="te",  # Telugu
    tha="th",  # Thai
    turn="tr",  # Turkish
    ukr="uk",  # Ukrainian
    urd="ur",  # Urdu
    uzb="uz",  # Uzbek
    vie="vi",  # Vietnamese
)


class WhatsAppType(ChannelType):
    """
    A WhatsApp Channel Type
    """

    code = "WA"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^wa/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"

    name = "WhatsApp"
    icon = "icon-whatsapp"

    claim_blurb = _(
        """If you have an enterprise WhatsApp account, you can connect it to communicate with your contacts"""
    )
    claim_view = ClaimView

    schemes = [WHATSAPP_SCHEME]
    max_length = 4096
    attachment_support = True

    def is_available_to(self, user):
        return user.groups.filter(name="Beta")

    def send(self, channel, msg, text):  # pragma: no cover
        raise Exception("Sending WhatsApp messages is only possible via Courier")

    def get_urls(self):
        return [self.get_claim_url(), url(r"^refresh/(?P<uuid>[a-z0-9\-]+)/?$", RefreshView.as_view(), name="refresh")]

    def activate(self, channel):
        domain = channel.org.get_brand_domain()
        headers = {"Authorization": "Bearer %s" % channel.config[Channel.CONFIG_AUTH_TOKEN]}

        # first set our callbacks
        payload = {"webhooks": {"url": "https://" + domain + reverse("courier.wa", args=[channel.uuid, "receive"])}}
        resp = requests.patch(
            channel.config[Channel.CONFIG_BASE_URL] + "/v1/settings/application", json=payload, headers=headers
        )

        if resp.status_code != 200:
            raise ValidationError(_("Unable to register callbacks: %s", resp.content))

        # update our quotas so we can send at 15/s
        payload = {
            "messaging_api_rate_limit": ["15", "54600", "1000000"],
            "contacts_scrape_rate_limit": "1000000",
            "contacts_api_rate_limit": ["15", "54600", "1000000"],
        }
        resp = requests.patch(
            channel.config[Channel.CONFIG_BASE_URL] + "/v1/settings/application", json=payload, headers=headers
        )

        if resp.status_code != 200:
            raise ValidationError(_("Unable to configure channel: %s", resp.content))
