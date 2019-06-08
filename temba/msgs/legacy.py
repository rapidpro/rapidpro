from temba.orgs.models import Language


def send_broadcast(
    bcast, *, expressions_context=None, response_to=None, msg_type="I", run_map=None, high_priority=False
):
    """
    Only used for testing to approximate how mailroom sends a broadcast
    """

    from temba.contacts.models import Contact
    from temba.msgs.models import Msg, SENT, UnreachableException

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
                message_context["contact"] = contact.build_expressions_context()
        else:
            message_context = None

        # add in our parent context if the message references @parent
        if run_map:
            run = run_map.get(contact.id, None)
            if run:
                if "parent" in text:
                    if run.parent:
                        message_context.update(dict(parent=run.parent.build_expressions_context()))

        try:
            Msg.create_outgoing(
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
