"""Upload one sticker pack to the dev sticker bucket."""

from __future__ import annotations

import json

import boto3
from botocore.exceptions import BotoCoreError, ClientError, ProfileNotFound

from sticker_pack import DEV_BUCKET, DEV_REGION, PackError, UploadObject


class StickerBucket:
    def __init__(self, profile: str, bucket: str = DEV_BUCKET, region: str = DEV_REGION) -> None:
        self.profile = (profile or "").strip()
        self.bucket = bucket
        self.region = region
        try:
            session = boto3.Session(profile_name=self.profile or None, region_name=region)
            self.client = session.client("s3")
        except ProfileNotFound as exc:
            raise PackError(f"AWS profile {self.profile} is not on this computer.") from exc

    def list_keys(self, pack_id: str) -> list[str]:
        prefix = f"packs/{pack_id}/"
        keys: list[str] = []
        token: str | None = None
        while True:
            args: dict = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                args["ContinuationToken"] = token
            page = self._call(self.client.list_objects_v2, **args)
            keys.extend(item["Key"] for item in page.get("Contents", []))
            if not page.get("IsTruncated"):
                return keys
            token = page.get("NextContinuationToken")

    def read_manifest(self, pack_id: str) -> dict | None:
        try:
            obj = self._call(self.client.get_object, Bucket=self.bucket, Key=f"packs/{pack_id}/manifest.json")
        except ClientError as exc:
            if _code(exc) in {"NoSuchKey", "404", "NotFound"}:
                return None
            raise PackError(_aws_message(exc)) from exc
        try:
            data = json.loads(obj["Body"].read().decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def put(self, item: UploadObject) -> None:
        self._call(
            self.client.put_object,
            Bucket=self.bucket,
            Key=item.key,
            Body=item.data,
            ContentType=item.content_type,
            CacheControl="public, max-age=60",
        )

    def delete(self, keys: list[str]) -> None:
        for start in range(0, len(keys), 1000):
            chunk = keys[start : start + 1000]
            self._call(
                self.client.delete_objects,
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": key} for key in chunk], "Quiet": True},
            )

    def _call(self, fn, **kwargs):
        try:
            return fn(**kwargs)
        except ClientError as exc:
            raise PackError(_aws_message(exc)) from exc
        except BotoCoreError as exc:
            raise PackError(f"Could not reach AWS: {exc}") from exc


def _code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


def _aws_message(exc: ClientError) -> str:
    code = _code(exc)
    if code in {"AccessDenied", "403"}:
        return (
            "This AWS profile cannot write to the dev sticker bucket "
            f"({DEV_BUCKET}). The dev account profile is vinit."
        )
    if code in {"NoSuchBucket", "404", "NotFound"}:
        return f"The dev sticker bucket {DEV_BUCKET} was not found."
    message = str(exc.response.get("Error", {}).get("Message") or code or "AWS rejected the upload.")
    return message
