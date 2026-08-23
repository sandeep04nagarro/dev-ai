"""Shared S3 client for the Langfuse trace archival script and proxy.

Empty S3_ENDPOINT / S3_ACCESS_KEY / S3_SECRET_KEY (the production default)
means "talk to real AWS S3 using the EC2 instance's IAM role" - boto3's
default credential chain resolves that automatically with no keys in config.
Set all three only for local/dev against MinIO.
"""

import boto3
from botocore.client import Config

from agent.utils.secrets import SecretsManager

S3_ENDPOINT = SecretsManager.get("S3_ENDPOINT", "")
S3_ACCESS_KEY = SecretsManager.get("S3_ACCESS_KEY", "")
S3_SECRET_KEY = SecretsManager.get("S3_SECRET_KEY", "")
S3_BUCKET = SecretsManager.get("S3_BUCKET", "langfuse")
AWS_REGION = SecretsManager.get("AWS_REGION", "us-east-1")


def get_s3_client():
    kwargs: dict = {"region_name": AWS_REGION}
    if S3_ENDPOINT:
        # MinIO doesn't support virtual-hosted-style addressing on localhost.
        kwargs["endpoint_url"] = S3_ENDPOINT
        kwargs["config"] = Config(s3={"addressing_style": "path"})
    if S3_ACCESS_KEY and S3_SECRET_KEY:
        kwargs["aws_access_key_id"] = S3_ACCESS_KEY
        kwargs["aws_secret_access_key"] = S3_SECRET_KEY
    return boto3.client("s3", **kwargs)
