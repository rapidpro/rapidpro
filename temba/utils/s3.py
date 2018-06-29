from storages.backends.s3boto3 import S3Boto3Storage


class PublicFileStorage(S3Boto3Storage):
    default_acl = "public-read"
    file_overwrite = False
