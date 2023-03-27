from temba.msgs.models import Attachment, Media, Q


# deserialize attachments from db to widget for populating initial values
def compose_deserialize_attachments(attachments):
    if not attachments or len(attachments) == 0:
        return []
    parsed_attachments = Attachment.parse_all(attachments)
    deserialized_attachments = []
    for parsed_attachment in parsed_attachments:
        media = Media.objects.filter(
            Q(content_type=parsed_attachment.content_type) and Q(url=parsed_attachment.url)
        ).first()
        deserialized_attachment = {
            "uuid": str(media.uuid),
            "content_type": media.content_type,
            "url": media.url,
            "filename": media.filename,
            "size": str(media.size),
        }
        deserialized_attachments.append(deserialized_attachment)
    return deserialized_attachments


# serialize attachments from widget to db for saving final  values
def compose_serialize_attachments(attachments):
    if not attachments or len(attachments) == 0:
        return []
    serialized_attachments = [f"{a['content_type']}:{a['url']}" for a in attachments]
    return serialized_attachments
