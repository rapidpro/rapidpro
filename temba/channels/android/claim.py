from django.utils import timezone

from temba.utils import countries, get_anonymous_user
from temba.utils.models import generate_uuid
from temba.utils.text import generate_secret

from ..models import Channel


class UnsupportedAndroidChannelError(Exception):
    def __init__(self, message):
        self.message = message


def generate_claim_code() -> str:
    """
    Generates a random and guaranteed unique claim code
    """
    code = generate_secret(9)
    while Channel.objects.filter(claim_code=code):  # pragma: no cover
        code = generate_secret(9)
    return code


def get_or_create_channel(registration_data, status):
    """
    Creates a new Android channel from the fcm and status commands sent during device registration
    """
    fcm_id = registration_data.get("fcm_id")
    uuid = registration_data.get("uuid")
    country = status.get("cc")
    device = status.get("dev")

    if not fcm_id or not uuid:
        gcm_id = registration_data.get("gcm_id")
        if gcm_id:
            raise UnsupportedAndroidChannelError("Unsupported Android client app.")
        else:
            raise ValueError("Can't create Android channel without UUID or FCM ID")

    # look for existing active channel with this UUID
    existing = Channel.objects.filter(uuid=uuid, is_active=True).first()

    # if device exists reset some of the settings (ok because device clearly isn't in use if it's registering)
    if existing:
        config = existing.config
        config.update({Channel.CONFIG_FCM_ID: fcm_id})
        existing.config = config
        existing.claim_code = generate_claim_code()
        existing.secret = Channel.generate_secret()
        existing.country = country
        existing.device = device
        existing.save(update_fields=("config", "secret", "claim_code", "country", "device"))

        return existing

    # if any inactive channel has this UUID, we can steal it
    for ch in Channel.objects.filter(uuid=uuid, is_active=False):
        ch.uuid = generate_uuid()
        ch.save(update_fields=("uuid",))

    # generate random secret and claim code
    claim_code = generate_claim_code()
    secret = Channel.generate_secret()
    anon = get_anonymous_user()
    config = {Channel.CONFIG_FCM_ID: fcm_id}

    return Channel.create(
        None,
        anon,
        country,
        Channel.get_type_from_code("A"),
        name=device[:64] if device else "Android",
        address=None,
        config=config,
        uuid=uuid,
        device=device,
        claim_code=claim_code,
        secret=secret,
        last_seen=timezone.now(),
    )


def claim_channel(org, channel, phone: str):
    """
    Claims this channel for the given org/user
    """

    if not channel.country:  # pragma: needs cover
        channel.country = countries.from_tel(phone)

    channel.org = org
    channel.is_active = True
    channel.claim_code = None
    channel.address = phone
    channel.save()

    org.normalize_contact_tels()
