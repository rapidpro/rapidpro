from tempfile import NamedTemporaryFile

import ffmpeg

from temba.utils.s3 import public_file_storage


def process_upload(media):
    content_type, path = _get_uploaded_content(media)
    media_type, sub_type = content_type.split("/")

    with public_file_storage.open(path, mode="rb") as stream:
        # download the media from storage to a local temp file
        with NamedTemporaryFile(suffix=media.name, delete=True) as temp:
            temp.write(stream.read())
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
    # if sub_type != "mp3":
    #    media.paths["audio/mp3"] = _convert_audio(path, "libmp3lame", "mp3")
    # if sub_type != "mp4":
    #    media.paths["audio/mp4"] = _convert_audio(path, "aac", "m4a")

    media.duration = _get_duration(file)


def _process_video_upload(media, sub_type: str, path: str, file):
    # TODO generate thumbnail

    media.duration = _get_duration(file)


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
