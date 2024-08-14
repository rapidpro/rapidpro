from dataclasses import dataclass

import polib
import pycountry

from django.core.files.storage import default_storage

from .uuid import uuid4


def po_get_path(org, uuid):
    return f"orgs/{org.id}/po_imports/{uuid}.po"


def po_save(org, data):
    uuid = str(uuid4())
    default_storage.save(po_get_path(org, uuid), data)
    return uuid


def po_load(org, uuid):
    file = default_storage.open(po_get_path(org, uuid))
    return file.read().decode()


@dataclass
class POInfo:
    language_name: str
    language_code: str
    num_entries: int
    num_translations: int


def po_get_info(data) -> POInfo:
    po = polib.pofile(data)

    language = None
    iso_code = po.metadata.get("Language-3", "")
    if iso_code:
        language = pycountry.languages.get(alpha_3=iso_code)
    iso_code = po.metadata.get("Language", "")
    if iso_code:
        language = pycountry.languages.get(alpha_2=iso_code)

    return POInfo(
        language_name=language.name if language else "",
        language_code=language.alpha_3 if language else "",
        num_entries=len(po),
        num_translations=len(po.translated_entries()),
    )
