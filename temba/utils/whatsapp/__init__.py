import logging

logger = logging.getLogger(__name__)


def update_api_version(channel):
    try:
        api_status = channel.get_type().check_health(channel)
        version = api_status["meta"]["version"]
        if not version.startswith("v"):
            version = "v" + version
        channel.config.update(version=version)
        channel.save()
    except Exception as e:
        logger.info(f"Error retrieving WhatsApp API version: {str(e)}", exc_info=True)
