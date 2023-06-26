import random
import re
import sys
from uuid import UUID, uuid4 as real_uuid4

default_generator = real_uuid4

UUID_REGEX = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def uuid4() -> UUID:
    return default_generator()


def seeded_generator(seed: int):
    """
    Returns a UUID v4 generation function which is backed by a RNG with the given seed
    """
    rng = random.Random(seed)

    def generator() -> UUID:
        data = []
        for i in range(4):
            integer = rng.getrandbits(4 * 8)
            data.extend(integer.to_bytes(4, sys.byteorder))
        return UUID(bytes=bytes(data), version=4)

    return generator


def is_uuid(val: str) -> bool:
    """
    Returns whether the given string is a valid UUID
    """
    try:
        UUID(str(val))
        return True
    except Exception:
        return False


def find_uuid(val: str) -> str | None:
    """
    Finds and returns the first valid UUID in the given string
    """
    match = UUID_REGEX.search(val)
    return match.group(0) if match else None
