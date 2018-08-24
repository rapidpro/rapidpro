from django.conf import settings


def reduce_event(event):
    new_event = copy_keys(event, {"type", "msg"})
    new_event["msg"] = copy_keys(event["msg"], {"text", "urn", "channel", "attachments"})
    new_msg = new_event["msg"]

    # legacy events are re-constructed from real messages which have their text stripped
    if new_msg["text"]:
        new_msg["text"] = new_msg["text"].strip()

    # legacy events have absolute paths for attachments, new have relative
    if "attachments" in new_msg:
        abs_prefix = f"https://{settings.AWS_BUCKET_DOMAIN}/"
        new_msg["attachments"] = [a.replace(abs_prefix, "") for a in new_msg["attachments"]]

    return new_event


def copy_keys(d, keys):
    return {k: v for k, v in d.items() if k in keys}
