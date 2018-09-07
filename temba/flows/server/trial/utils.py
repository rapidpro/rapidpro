from django.conf import settings

from temba.contacts.models import URN


def reduce_event(event):
    new_event = copy_keys(event, {"type", "msg"})

    if "msg" in new_event:
        new_event["msg"] = copy_keys(event["msg"], {"text", "urn", "channel", "attachments"})
        new_msg = new_event["msg"]

        # legacy events are re-constructed from real messages which have their text stripped
        if "text" in new_msg:
            new_msg["text"] = new_msg["text"].strip()

        # legacy events have absolute paths for attachments, new have relative
        if "attachments" in new_msg:
            abs_prefix = f"https://{settings.AWS_BUCKET_DOMAIN}/"
            new_msg["attachments"] = [a.replace(abs_prefix, "") for a in new_msg["attachments"]]

        # new engine events might have params on URNs that we're not interested in
        if "urn" in new_msg:
            scheme, path, query, fragment = URN.to_parts(new_msg["urn"])
            new_msg["urn"] = URN.from_parts(scheme, path, None, fragment)

    return new_event


def copy_keys(d, keys):
    return {k: v for k, v in d.items() if k in keys}
