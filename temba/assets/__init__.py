from __future__ import absolute_import, unicode_literals

from enum import Enum
from .handlers import RecordingAssetHandler, ExportContactsAssetHandler, ExportMessagesAssetHandler, ExportResultsAssetHandler


class AssetType(Enum):
    recording = (RecordingAssetHandler,)
    contact_export = (ExportContactsAssetHandler,)
    results_export = (ExportResultsAssetHandler,)
    message_export = (ExportMessagesAssetHandler,)

    def __init__(self, handler):
        self.handler = handler

    def get_handler(self):
        return self.handler()
