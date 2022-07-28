import os
from tempfile import NamedTemporaryFile

import ffmpeg

from temba.utils.s3 import public_file_storage
from temba.utils.uuid import uuid4

from .models import Media


def process_upload(media: Media):
    media_type, sub_type = media.content_type.split("/")

    with public_file_storage.open(media.path, mode="rb") as stream:
        # download the media from storage to a local temp file
        with NamedTemporaryFile(suffix=media.name, delete=True) as temp:
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

    media.is_ready = True
    media.save()


def _process_image_upload(media: Media, sub_type: str, file):
    pass


def _process_audio_upload(media: Media, sub_type: str, file):
    if sub_type != "mp3":
        _create_alternate_audio(media, file, "audio/mp3", "mp3", codec="libmp3lame")
    if sub_type != "mp4":
        _create_alternate_audio(media, file, "audio/mp4", "m4a", codec="aac")

    media.duration = _get_duration(file)


def _process_video_upload(media: Media, sub_type: str, file):
    # media.paths["image/jpg"] = _create_new_video_thumbnail(media.path, file)

    media.duration = _get_duration(file)


def _create_alternate_audio(original: Media, file, new_content_type: str, new_extension: str, codec: str) -> str:
    """
    Creates a new audio version of the given media file
    """

    def transform(in_name, out_name):
        ffmpeg.input(in_name).output(out_name, acodec=codec).overwrite_output().run()

    return _create_new_media(original, file, transform, new_extension)


def _create_new_video_thumbnail(media: str, file) -> str:
    """
    Creates a new thumbnail for the given video file
    """

    def transform(in_name, out_name):
        ffmpeg.input(in_name, ss="00:00:00").filter("scale", "640", -1).output(
            out_name, vframes=1
        ).overwrite_output().run()

    return _create_new_media(media, file, transform, "jpg")


def _create_new_media(original: Media, file, transform, new_content_type: str, new_extension: str) -> Media:
    """
    Creates a new media instance by transforming an original with an ffmpeg pipeline
    """

    new_path = _change_extension(original.path, new_extension)

    with NamedTemporaryFile(suffix="." + new_extension, delete=True) as temp:
        transform(file.name, temp.name)

        public_file_storage.save(new_path, temp)

    return Media.objects.create(
        uuid=uuid4(),
        org=original.org,
        url=public_file_storage.url(new_path),
        name=file.name,
        content_type=new_content_type,
        path=new_path,
        original=original,
        created_by=original.created_by,
    )


def _get_duration(file) -> int:
    """
    Uses ffprobe to get the duration of the audio or video file
    """
    output = ffmpeg.probe(file.name)
    return int(float(output["format"]["duration"]) * 1000)


def _change_extension(filename: str, extension: str) -> str:
    """
    Changes the extension of a filename
    """
    return os.path.splitext(filename)[0] + "." + extension
