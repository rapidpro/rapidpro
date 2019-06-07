from temba.orgs.models import Language


def send_broadcast(
    bcast, *, expressions_context=None, response_to=None, msg_type="I", run_map=None, high_priority=False
):
    """
    Only used for testing to approximate how mailroom sends a broadcast
    """

    from temba.msgs.models import Msg, SENT

    contacts = set(bcast.contacts.all())
    for group in bcast.groups.all():
        contacts.update(group.contacts.all())

    urns = set(bcast.urns.all())
    for contact in contacts:
        if bcast.send_all:
            urns.update(contact.urns.all())
        else:
            urns.add(contact.urns.first())

    for urn in urns:
        text = bcast.get_translated_text(urn.contact)
        media = get_translated_media(bcast, urn.contact)
        quick_replies = get_translated_quick_replies(bcast, urn.contact)

        if expressions_context is not None:
            message_context = expressions_context.copy()
            if "contact" not in message_context:
                message_context["contact"] = urn.contact.build_expressions_context()
        else:
            message_context = None

        # add in our parent context if the message references @parent
        if run_map:
            run = run_map.get(urn.contact.id, None)
            if run and run.flow:
                # a bit kludgy here, but should avoid most unnecessary context creations.
                # since this path is an optimization for flow starts, we don't need to
                # worry about the @child context.
                if "parent" in text:
                    if run.parent:
                        message_context.update(dict(parent=run.parent.build_expressions_context()))

        Msg.create_outgoing(
            bcast.org,
            bcast.created_by,
            urn,
            text,
            bcast,
            attachments=[media] if media else None,
            quick_replies=quick_replies,
            response_to=response_to,
            high_priority=high_priority,
            msg_type=msg_type,
            expressions_context=message_context,
        )

    bcast.recipient_count = len(urns)
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
