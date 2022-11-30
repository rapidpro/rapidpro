TYPES = {}


def register_notification_type(typ):
    assert typ.slug not in TYPES, f"type {typ.slug} is already registered"

    TYPES[typ.slug] = typ
