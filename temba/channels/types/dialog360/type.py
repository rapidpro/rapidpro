import requests

from django.forms import ValidationError
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import Channel
from temba.channels.types.dialog360.views import ClaimView
from temba.contacts.models import URN

from ...models import ChannelType

STATUS_MAPPING = dict(
    submitted=TemplateTranslation.STATUS_PENDING,
    approved=TemplateTranslation.STATUS_APPROVED,
    rejected=TemplateTranslation.STATUS_REJECTED,
)
LANGUAGE_MAPPING = dict(
    af=("afr", None),  # Afrikaans
    sq=("sqi", None),  # Albanian
    ar=("ara", None),  # Arabic
    az=("aze", None),  # Azerbaijani
    bn=("ben", None),  # Bengali
    bg=("bul", None),  # Bulgarian
    ca=("cat", None),  # Catalan
    zh_CN=("zho", "CN"),  # Chinese (CHN)
    zh_HK=("zho", "HK"),  # Chinese (HKG)
    zh_TW=("zho", "TW"),  # Chinese (TAI)
    hr=("hrv", None),  # Croatian
    cs=("ces", None),  # Czech
    da=("dah", None),  # Danish
    nl=("nld", None),  # Dutch
    en=("eng", None),  # English
    en_GB=("eng", "GB"),  # English (UK)
    en_US=("eng", "US"),  # English (US)
    et=("est", None),  # Estonian
    fil=("fil", None),  # Filipino
    fi=("fin", None),  # Finnish
    fr=("fra", None),  # French
    de=("deu", None),  # German
    el=("ell", None),  # Greek
    gu=("gul", None),  # Gujarati
    ha=("hau", None),  # Hausa
    he=("enb", None),  # Hebrew
    hi=("hin", None),  # Hindi
    hu=("hun", None),  # Hungarian
    id=("ind", None),  # Indonesian
    ga=("gle", None),  # Irish
    it=("ita", None),  # Italian
    ja=("jpn", None),  # Japanese
    kn=("kan", None),  # Kannada
    kk=("kaz", None),  # Kazakh
    ko=("kor", None),  # Korean
    lo=("lao", None),  # Lao
    lv=("lav", None),  # Latvian
    lt=("lit", None),  # Lithuanian
    ml=("mal", None),  # Malayalam
    mk=("mkd", None),  # Macedonian
    ms=("msa", None),  # Malay
    mr=("mar", None),  # Marathi
    nb=("nob", None),  # Norwegian
    fa=("fas", None),  # Persian
    pl=("pol", None),  # Polish
    pt_BR=("por", "BR"),  # Portuguese (BR)
    pt_PT=("por", "PT"),  # Portuguese (POR)
    pa=("pan", None),  # Punjabi
    ro=("ron", None),  # Romanian
    ru=("rus", None),  # Russian
    sr=("srp", None),  # Serbian
    sk=("slk", None),  # Slovak
    sl=("slv", None),  # Slovenian
    es=("spa", None),  # Spanish
    es_AR=("spa", "AR"),  # Spanish (ARG)
    es_ES=("spa", "ES"),  # Spanish (SPA)
    es_MX=("spa", "MX"),  # Spanish (MEX)
    sw=("swa", None),  # Swahili
    sv=("swe", None),  # Swedish
    ta=("tam", None),  # Tamil
    te=("tel", None),  # Telugu
    th=("tha", None),  # Thai
    tr=("tur", None),  # Turkish
    uk=("ukr", None),  # Ukrainian
    ur=("urd", None),  # Urdu
    uz=("uzb", None),  # Uzbek
    vi=("vie", None),  # Vietnamese]
    zu=("zul", None),  # Zulu
)


class Dialog360Type(ChannelType):
    """
    A 360 Dialog Channel Type
    """

    code = "D3"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^d3/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"

    name = "360Dialog WhatsApp"
    icon = "icon-whatsapp"

    claim_blurb = _(
        "Activate your own enterprise WhatsApp account in %(link)s to communicate with your contacts. "
    ) % {"link": '<a href="https://www.360dialog.com/">360Dialog</a>'}
    claim_view = ClaimView

    schemes = [URN.WHATSAPP_SCHEME]
    max_length = 4096
    attachment_support = True

    def activate(self, channel):
        domain = channel.org.get_brand_domain()
        headers = {"D360-API-KEY": channel.config[Channel.CONFIG_AUTH_TOKEN], "Content-Type": "application/json"}

        # first set our callbacks
        payload = {"url": "https://" + domain + reverse("courier.d3", args=[channel.uuid, "receive"])}
        resp = requests.post(
            channel.config[Channel.CONFIG_BASE_URL] + "/v1/configs/webhook", json=payload, headers=headers
        )

        if resp.status_code != 200:
            raise ValidationError(_("Unable to register callbacks: %(resp)s"), params={"resp": resp.content})
