import json

from temba.msgs.models import Attachment, Media, Q


def compose_serialize(translation=None, json_encode=False):
    """
    Serializes attachments from db to compose widget for populating initial widget values
    """
    if not translation:
        text = ""
        attachments = []
    else:
        text = translation["text"]
        attachments = compose_serialize_attachments(translation["attachments"])

    serialized = {"text": text, "attachments": attachments}

    if json_encode:
        return json.dumps(serialized)

    return serialized


def compose_serialize_attachments(attachments):
    if not attachments:
        return []
    parsed_attachments = Attachment.parse_all(attachments)
    serialized_attachments = []
    for parsed_attachment in parsed_attachments:
        media = Media.objects.filter(
            Q(content_type=parsed_attachment.content_type) and Q(url=parsed_attachment.url)
        ).first()
        serialized_attachment = {
            "uuid": str(media.uuid),
            "content_type": media.content_type,
            "url": media.url,
            "filename": media.filename,
            "size": str(media.size),
        }
        serialized_attachments.append(serialized_attachment)
    return serialized_attachments


def compose_deserialize(compose):
    """
    Deserializes attachments from compose widget to db for saving final db values
    """
    text = compose["text"]
    attachments = compose_deserialize_attachments(compose["attachments"])
    return text, attachments


def compose_deserialize_attachments(attachments):
    if not attachments:
        return []
    return [f"{a['content_type']}:{a['url']}" for a in attachments]
