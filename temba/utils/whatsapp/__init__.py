import logging

logger = logging.getLogger(__name__)


def update_api_version(channel):
    response = channel.type.check_health(channel)
    if not response:
        logger.debug("Error retrieving WhatsApp API version: Failed to check health")
        return
    api_status = response.json()
    version = api_status["meta"]["version"]
    if not version.startswith("v"):
        version = "v" + version
    channel.config.update(version=version)
    channel.save(update_fields=("config",))
