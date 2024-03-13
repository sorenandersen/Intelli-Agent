import json
import logging
import math

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3_client = boto3.client("s3")

supported_file_types = ["pdf", "txt", "doc", "md", "html", "json", "jsonl", "csv"]


def get_job_number(event, file_count):
    job_number = event.get("JobNumber", 50)

    if file_count < job_number:
        job_number = file_count

    return job_number


# Offline lambda function to count the number of files in the S3 bucket
def lambda_handler(event, context):
    logger.info(f"event:{event}")
    # Retrieve bucket name and prefix from the event object passed by Step Function
    bucket_name = event["s3Bucket"]
    prefix = event["s3Prefix"]
    # fetch index from event with default value none
    workspace_id = event["workspaceId"]

    # Initialize the file count
    file_count = 0

    # Paginate through the list of objects in the bucket with the specified prefix
    paginator = s3_client.get_paginator("list_objects_v2")
    page_iterator = paginator.paginate(Bucket=bucket_name, Prefix=prefix)

    # Count the files, note skip the prefix with slash, which is the folder name
    for page in page_iterator:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            file_type = key.split(".")[-1].lower()  # Extract file extension
            if key.endswith("/") or file_type not in supported_file_types:
                continue

            file_count += 1

    job_number = get_job_number(event, file_count)

    batch_file_number = file_count // job_number + 1

    # convert the fileCount into an array of numbers "fileIndices": [0, 1, 2, ..., 10], an array from 0 to fileCount-1
    batch_indices = list(range(job_number))

    # This response should match the expected input schema of the downstream tasks in the Step Functions workflow
    return {
        "s3Bucket": bucket_name,
        "s3Prefix": prefix,
        "fileCount": file_count,
        "workspaceId": workspace_id,
        "qaEnhance": event["qaEnhance"].lower() if "qaEnhance" in event else "false",
        "offline": event["offline"].lower(),
        "batchFileNumber": str(batch_file_number),
        "batchIndices": batch_indices,
    }
