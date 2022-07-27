import os
from tempfile import NamedTemporaryFile

import ffmpeg

from temba.utils.s3 import public_file_storage


def process_upload(media):
    content_type, path = _get_uploaded_content(media)
    media_type, sub_type = content_type.split("/")

    with public_file_storage.open(path, mode="rb") as stream:
        # download the media from storage to a local temp file
        with NamedTemporaryFile(suffix=media.name, delete=True) as temp:
            data = stream.read()
            temp.write(data)
            temp.flush()
            temp.seek(0)

            if media_type == "image":
                _process_image_upload(media, sub_type, path, temp)
            elif media_type == "audio":
                _process_audio_upload(media, sub_type, path, temp)
            elif media_type == "video":
                _process_video_upload(media, sub_type, path, temp)


def _process_image_upload(media, sub_type, path, file):
    pass


def _process_audio_upload(media, sub_type, path, file):
    if sub_type != "mp3":
        media.paths["audio/mp3"] = _create_new_audio_version(path, file, "mp3", codec="libmp3lame")
    if sub_type != "mp4":
        media.paths["audio/mp4"] = _create_new_audio_version(path, file, "m4a", codec="aac")

    media.duration = _get_duration(file)


def _process_video_upload(media, sub_type: str, path: str, file):
    media.paths["image/jpg"] = _create_new_video_thumbnail(path, file)

    media.duration = _get_duration(file)


def _create_new_audio_version(path: str, file, new_extension: str, codec: str) -> str:
    """
    Creates a new audio version of the given media file
    """

    def transform(in_name, out_name):
        ffmpeg.input(in_name).output(out_name, acodec=codec).overwrite_output().run()

    return _create_new_file(path, file, new_extension, transform)


def _create_new_video_thumbnail(path: str, file) -> str:
    """
    Creates a new thumbnail for the given video file
    """

    def transform(in_name, out_name):
        ffmpeg.input(in_name, ss="00:00:00").filter("scale", "640", -1).output(
            out_name, vframes=1
        ).overwrite_output().run()

    return _create_new_file(path, file, "jpg", transform)


def _create_new_file(path: str, file, new_extension: str, transform) -> str:
    """
    Creates a new file from the given media file using the given transform function
    """

    new_path = _change_extension(path, new_extension)

    with NamedTemporaryFile(suffix="." + new_extension, delete=True) as temp:
        transform(file.name, temp.name)

        public_file_storage.save(new_path, temp)

    return new_path


def _get_duration(file) -> int:
    """
    Uses ffprobe to get the duration of the audio or video file
    """
    output = ffmpeg.probe(file.name)
    return int(float(output["format"]["duration"]) * 1000)


def _get_uploaded_content(media) -> tuple:
    """
    Gets the content type and path of the uploaded media
    """
    content_type = list(media.paths.keys())[0]
    return content_type, media.paths[content_type]


def _change_extension(filename: str, extension: str) -> str:
    """
    Changes the extension of a filename
    """
    return os.path.splitext(filename)[0] + "." + extension
