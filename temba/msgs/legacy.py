from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.utils import timezone

from temba.channels.models import Channel
from temba.contacts.models import TEL_SCHEME, URN, Contact, ContactURN
from temba.flows.legacy.expressions import channel_context, evaluate
from temba.msgs.models import SENT, Msg, UnreachableException
from temba.orgs.models import Language


def send_broadcast(bcast, *, expressions_context=None, response_to=None, msg_type="I", high_priority=False):
    """
    Only used for testing to approximate how mailroom sends a broadcast
    """

    contacts = set(bcast.contacts.all())
    for group in bcast.groups.all():
        contacts.update(group.contacts.all())

    recipients = set(bcast.urns.all())

    for contact in contacts:
        if bcast.send_all:
            recipients.update(contact.urns.all())
        else:
            recipients.add(contact)

    for recipient in recipients:
        contact = recipient if isinstance(recipient, Contact) else recipient.contact

        text = bcast.get_translated_text(contact)
        media = get_translated_media(bcast, contact)
        quick_replies = get_translated_quick_replies(bcast, contact)

        if expressions_context is not None:
            message_context = expressions_context.copy()
            if "contact" not in message_context:
                from temba.flows.legacy.expressions import contact_context

                message_context["contact"] = contact_context(contact)
        else:
            message_context = None

        try:
            _create_outgoing(
                bcast.org,
                bcast.created_by,
                recipient,
                text,
                bcast,
                channel=bcast.channel,
                attachments=[media] if media else None,
                quick_replies=quick_replies,
                response_to=response_to,
                high_priority=high_priority,
                msg_type=msg_type,
                expressions_context=message_context,
            )
        except UnreachableException:
            pass

    bcast.recipient_count = len(recipients)
    bcast.status = SENT
    bcast.save(update_fields=("recipient_count", "status"))

    bcast.org.trigger_send(bcast.msgs.all())


def _create_outgoing(
    org,
    user,
    recipient,
    text,
    broadcast=None,
    channel=None,
    high_priority=False,
    sent_on=None,
    response_to=None,
    expressions_context=None,
    status="P",
    insert_object=True,
    attachments=None,
    topup_id=None,
    msg_type="I",
    connection=None,
    quick_replies=None,
    uuid=None,
):
    if not org or not user:
        raise ValueError("Trying to create outgoing message with no org or user")

    # for IVR messages we need a channel that can call
    if msg_type == "V":
        role = Channel.ROLE_CALL
    else:
        role = Channel.ROLE_SEND

    # if message will be sent, resolve the recipient to a contact and URN
    contact, contact_urn = _resolve_recipient(org, user, recipient, channel, role=role)

    if not contact_urn:
        raise UnreachableException("No suitable URN found for contact")

    if not channel:
        if msg_type == "V":
            channel = org.get_call_channel()
        else:
            channel = org.get_send_channel(contact_urn=contact_urn)

        if not channel:  # pragma: needs cover
            raise UnreachableException("No suitable channel available for this org")

    # evaluate expressions in the text and attachments if a context was provided
    if expressions_context is not None:
        # make sure 'channel' is populated if we have a channel
        if channel and "channel" not in expressions_context:
            expressions_context["channel"] = channel_context(channel)

        (text, errors) = evaluate(text, expressions_context, org=org)
        if text:
            text = text[: Msg.MAX_TEXT_LEN]

        evaluated_attachments = []
        if attachments:
            for attachment in attachments:
                (attachment, errors) = evaluate(attachment, expressions_context, org=org)
                evaluated_attachments.append(attachment)
    else:
        text = text[: Msg.MAX_TEXT_LEN]
        evaluated_attachments = attachments

    # prefer none to empty lists in the database
    if evaluated_attachments is not None and len(evaluated_attachments) == 0:
        evaluated_attachments = None

    # if we are doing a single message, check whether this might be a loop of some kind
    if insert_object and status != "S" and getattr(settings, "DEDUPE_OUTGOING", True):
        # prevent the loop of message while the sending phone is the channel
        # get all messages with same text going to same number
        same_msgs = Msg.objects.filter(
            contact_urn=contact_urn,
            channel=channel,
            attachments=evaluated_attachments,
            text=text,
            direction="O",
            created_on__gte=timezone.now() - timedelta(minutes=10),
        )

        # we aren't considered with robo detection on calls
        same_msg_count = same_msgs.exclude(msg_type="V").count()

        if same_msg_count >= 10:
            return None

        # be more aggressive about short codes for duplicate messages
        # we don't want machines talking to each other
        tel = contact.get_urn(TEL_SCHEME)
        if tel:
            tel = tel.path

        if tel and len(tel) < 6:
            same_msg_count = Msg.objects.filter(
                contact_urn=contact_urn,
                channel=channel,
                text=text,
                direction="O",
                created_on__gte=timezone.now() - timedelta(hours=24),
            ).count()
            if same_msg_count >= 10:  # pragma: needs cover
                return None

    # costs 1 credit to send a message
    if not topup_id:
        (topup_id, _) = org.decrement_credit()

    if response_to:
        msg_type = response_to.msg_type

    text = text.strip()

    metadata = {}  # init metadata to the same as the default value of the Msg.metadata field
    if quick_replies:
        for counter, reply in enumerate(quick_replies):
            (value, errors) = evaluate(text=reply, context=expressions_context, org=org)
            if value:
                quick_replies[counter] = value
        metadata = dict(quick_replies=quick_replies)

    msg_args = dict(
        uuid=uuid or uuid4(),
        contact=contact,
        contact_urn=contact_urn,
        org=org,
        channel=channel,
        text=text,
        created_on=timezone.now(),
        modified_on=timezone.now(),
        direction="O",
        status=status,
        broadcast=broadcast,
        response_to=response_to,
        msg_type=msg_type,
        high_priority=high_priority,
        attachments=evaluated_attachments,
        metadata=metadata,
        connection=connection,
    )

    if sent_on:
        msg_args["sent_on"] = sent_on

    if topup_id is not None:
        msg_args["topup_id"] = topup_id

    return Msg.objects.create(**msg_args) if insert_object else Msg(**msg_args)


def _resolve_recipient(org, user, recipient, channel, role=Channel.ROLE_SEND):
    """
    Recipient can be a contact, a URN object, or a URN tuple, e.g. ('tel', '123'). Here we resolve the contact and
    contact URN to use for an outgoing message.
    """
    contact = None
    contact_urn = None

    resolved_schemes = set(channel.schemes) if channel else org.get_schemes(role)

    if isinstance(recipient, Contact):
        contact = recipient
        contact_urn = contact.get_urn(schemes=resolved_schemes)  # use highest priority URN we can send to
    elif isinstance(recipient, ContactURN):
        if recipient.scheme in resolved_schemes:
            contact = recipient.contact
            contact_urn = recipient
    elif isinstance(recipient, str):
        scheme, path, query, display = URN.to_parts(recipient)
        if scheme in resolved_schemes:
            contact, contact_urn = Contact.get_or_create(org, recipient, user=user)
    else:  # pragma: no cover
        raise ValueError("Message recipient must be a Contact, ContactURN or URN string")

    return contact, contact_urn


def get_translated_media(bcast, contact, org=None):
    """
    Gets the appropriate media for the given contact
    """
    preferred_languages = bcast._get_preferred_languages(contact, org)
    return Language.get_localized_text(bcast.media, preferred_languages)


def get_translated_quick_replies(bcast, contact, org=None):
    """
    Gets the appropriate quick replies translation for the given contact
    """
    preferred_languages = bcast._get_preferred_languages(contact, org)
    language_metadata = []
    metadata = bcast.metadata

    for item in metadata.get(bcast.METADATA_QUICK_REPLIES, []):
        text = Language.get_localized_text(text_translations=item, preferred_languages=preferred_languages)
        language_metadata.append(text)

    return language_metadata
