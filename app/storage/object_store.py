"""MinIO 对象存储：保存原始文件。

原文必须留档 —— 引用溯源要能跳回原文，解析器换版本后也要能重跑。
"""

from __future__ import annotations

import logging

import boto3
from botocore.client import Config

from app.config import settings

logger = logging.getLogger(__name__)


class ObjectStore:
    def __init__(self) -> None:
        self.client = boto3.client(
            "s3",
            endpoint_url=(
                f"{'https' if settings.minio_secure else 'http'}://{settings.minio_endpoint}"
            ),
            aws_access_key_id=settings.minio_access_key,
            aws_secret_access_key=settings.minio_secret_key,
            region_name="us-east-1",
            config=Config(signature_version="s3v4"),
        )
        self.bucket = settings.minio_bucket

    def ensure_bucket(self) -> None:
        from botocore.exceptions import ClientError

        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.client.create_bucket(Bucket=self.bucket)
            logger.info("已创建 bucket: %s", self.bucket)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self.ensure_bucket()
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return key

    def get(self, key: str) -> bytes:
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"].read()

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False
