"""Where book files live. The rest of the app only knows keys like users/1/books/5/original.pdf.

Default: the local data folder.
With S3_BUCKET set: an S3-compatible bucket holds the files, and the local folder is only a cache.
S3_ENDPOINT_URL points at a non-AWS store (MinIO, R2, ...). Credentials come from the usual AWS settings.
"""
import os
import shutil
from pathlib import Path

DATA = Path(os.environ.get("DATA_DIR", Path(__file__).parent.parent / "data"))
BUCKET = os.environ.get("S3_BUCKET")
_s3 = {}


def _client():
    if "client" not in _s3:
        import boto3  # only needed with a bucket
        _s3["client"] = boto3.client("s3", endpoint_url=os.environ.get("S3_ENDPOINT_URL") or None)
    return _s3["client"]


def book_key(user_id: int, book_id: int) -> str:
    return f"users/{user_id}/books/{book_id}/original.pdf"


def put(key: str, src: Path) -> None:
    """Move a finished upload into storage."""
    dest = DATA / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    src.replace(dest)
    if BUCKET:
        _client().upload_file(str(dest), BUCKET, key)


def local(key: str) -> str:
    """A path on this machine for the file. PyMuPDF and the PDF viewer both need a real file."""
    path = DATA / key
    if not path.exists() and BUCKET:
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part")  # so a broken download is never taken for the file
        _client().download_file(BUCKET, key, str(part))
        part.replace(path)
    return str(path)


def delete(prefix: str) -> None:
    """Remove everything under a prefix, for example users/1 or users/1/books/5."""
    shutil.rmtree(DATA / prefix, ignore_errors=True)
    if BUCKET:
        pages = _client().get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix + "/")
        for page in pages:
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                _client().delete_objects(Bucket=BUCKET, Delete={"Objects": keys})
