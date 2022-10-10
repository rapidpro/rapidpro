import logging
import os
from tempfile import NamedTemporaryFile

import ffmpeg

from temba.utils.s3 import public_file_storage

from .models import Media

logger = logging.getLogger(__name__)


def process_upload(media: Media):
    media_type, sub_type = media.content_type.split("/")

    try:
        with public_file_storage.open(media.path, mode="rb") as stream:
            # download the media from storage to a local temp file
            with NamedTemporaryFile(suffix=os.path.basename(media.path), delete=True) as temp:
                data = stream.read()
                temp.write(data)
                temp.flush()
                temp.seek(0)

                media.size = len(data)

                if media_type == "image":
                    _process_image_upload(media, sub_type, temp)
                elif media_type == "audio":
                    _process_audio_upload(media, sub_type, temp)
                elif media_type == "video":
                    _process_video_upload(media, sub_type, temp)

        media.status = Media.STATUS_READY
    except Exception as e:
        media.status = Media.STATUS_FAILED
        logger.error("Error processing media upload: %s", e, exc_info=True)

    media.save()


def _process_image_upload(media: Media, sub_type: str, file):
    stream_info = _get_stream_info(file.name, "v:0")
    media.width = stream_info.get("width", 0)
    media.height = stream_info.get("height", 0)


def _process_audio_upload(media: Media, sub_type: str, file):
    stream_info = _get_stream_info(file.name, "a:0")
    media.duration = int(float(stream_info.get("duration", 0)) * 1000)

    if sub_type != "mp3":
        _create_alternate_audio(media, file, "audio/mp3", "mp3", codec="libmp3lame")
    if sub_type != "mp4":
        _create_alternate_audio(media, file, "audio/mp4", "m4a", codec="aac")


def _process_video_upload(media: Media, sub_type: str, file):
    stream_info = _get_stream_info(file.name, "v:0")
    media.duration = int(float(stream_info.get("duration", 0)) * 1000)
    media.width = stream_info.get("width", 0)
    media.height = stream_info.get("height", 0)

    _create_video_thumbnail(media, file)


def _create_alternate_audio(original: Media, file, new_content_type: str, new_extension: str, codec: str) -> str:
    """
    Creates a new audio version of the given audio media
    """

    def transform(in_name, out_name):
        ffmpeg.input(in_name).output(out_name, acodec=codec).overwrite_output().run()

    return _create_alternate(original, file, transform, new_content_type, new_extension, duration=original.duration)


def _create_video_thumbnail(original: Media, file) -> str:
    """
    Creates a new thumbnail for the given video media
    """

    def transform(in_name, out_name):
        ffmpeg.input(in_name, ss="00:00:00").output(out_name, vframes=1).overwrite_output().run()

    return _create_alternate(
        original, file, transform, "image/jpeg", "jpg", width=original.width, height=original.height
    )


def _create_alternate(original: Media, file, transform, new_content_type: str, new_extension: str, **kwargs) -> Media:
    """
    Creates a new media instance by transforming an original with an ffmpeg pipeline
    """

    filename = _change_extension(original.filename, new_extension)

    with NamedTemporaryFile(suffix="." + new_extension, delete=True) as temp:
        transform(file.name, temp.name)

        return Media.create_alternate(original, filename, new_content_type, temp, **kwargs)


def _get_stream_info(filename: str, stream_id: str) -> dict:
    """
    Probes a file for a given stream type
    """
    probe = ffmpeg.probe(filename, select_streams=stream_id)
    return probe["streams"][0] if probe["streams"] else {}


def _change_extension(filename: str, extension: str) -> str:
    """
    Changes the extension of a filename
    """
    return os.path.splitext(filename)[0] + "." + extension
