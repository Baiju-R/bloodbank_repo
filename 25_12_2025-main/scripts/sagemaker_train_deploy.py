"""
Amazon SageMaker – Train & Deploy the BloodBridge Donor Recommender Model.

This script:
  1. Uploads training data (CSV) to S3.
  2. Launches an XGBoost training job on SageMaker.
  3. Deploys the trained model as a real-time inference endpoint.

Prerequisites
─────────────
- AWS CLI configured with credentials that have SageMaker + S3 access.
- An S3 bucket for training artefacts (set via --bucket).
- Training data exported via:  python manage.py export_training_data --output training_data.csv --include-labels --no-header
- IAM role with SageMaker, S3, and CloudWatch permissions (set via --role-arn).

Usage
─────
    python scripts/sagemaker_train_deploy.py \
        --bucket my-sagemaker-bucket \
        --role-arn arn:aws:iam::123456789012:role/SageMakerRole \
        --training-data training_data.csv \
        --region ap-south-1

    # To skip deployment and only train:
        --train-only

    # To deploy an already-trained model:
        --deploy-only --model-artefact s3://bucket/output/model.tar.gz
"""

import argparse
import os
import sys
import time

import boto3
from botocore.exceptions import ClientError

# ─── Constants ────────────────────────────────────────────────────────────────
ENDPOINT_NAME = "bloodbridge-donor-recommender"
TRAINING_JOB_PREFIX = "bloodbridge-xgb"
INSTANCE_TYPE_TRAIN = "ml.m5.large"
INSTANCE_TYPE_DEPLOY = "ml.t2.medium"  # cost-effective for low-traffic


def parse_args():
    p = argparse.ArgumentParser(description="Train & deploy BloodBridge XGBoost model on SageMaker")
    p.add_argument("--bucket", required=True, help="S3 bucket for training artefacts")
    p.add_argument("--role-arn", required=True, help="SageMaker execution IAM role ARN")
    p.add_argument("--training-data", default="training_data.csv", help="Local CSV path")
    p.add_argument("--region", default="ap-south-1", help="AWS region")
    p.add_argument("--train-only", action="store_true", help="Only train, do not deploy")
    p.add_argument("--deploy-only", action="store_true", help="Only deploy an existing model")
    p.add_argument("--model-artefact", help="S3 URI of model.tar.gz (for --deploy-only)")
    p.add_argument("--endpoint-name", default=ENDPOINT_NAME, help="SageMaker endpoint name")
    p.add_argument("--max-runtime", type=int, default=3600, help="Max training time in seconds")
    # XGBoost hyperparameters
    p.add_argument("--num-round", type=int, default=150, help="XGBoost boosting rounds")
    p.add_argument("--max-depth", type=int, default=5, help="XGBoost max tree depth")
    p.add_argument("--eta", type=float, default=0.1, help="XGBoost learning rate")
    p.add_argument("--subsample", type=float, default=0.8, help="XGBoost subsample ratio")
    p.add_argument("--min-child-weight", type=int, default=3, help="XGBoost min child weight")
    p.add_argument("--eval-metric", default="auc", help="XGBoost evaluation metric")
    return p.parse_args()


def upload_training_data(s3_client, bucket, local_path, prefix="bloodbridge/training"):
    """Upload CSV to S3 and return the S3 URI."""
    key = f"{prefix}/train.csv"
    print(f"Uploading {local_path} → s3://{bucket}/{key} ...")
    s3_client.upload_file(local_path, bucket, key)
    return f"s3://{bucket}/{key}"


def get_xgboost_image_uri(region):
    """Return the SageMaker XGBoost built-in algorithm container URI."""
    # XGBoost 1.7 container (latest stable). SageMaker maintains per-region ECR images.
    account_map = {
        "us-east-1": "683313688378",
        "us-east-2": "257758044811",
        "us-west-1": "746614075791",
        "us-west-2": "246618743249",
        "eu-west-1": "720646828776",
        "eu-west-2": "644912444149",
        "eu-central-1": "492215442770",
        "ap-south-1": "720646828776",
        "ap-southeast-1": "121021644041",
        "ap-southeast-2": "783357654285",
        "ap-northeast-1": "354813040037",
        "ap-northeast-2": "306986355934",
    }
    account = account_map.get(region)
    if not account:
        # Fallback: use the SageMaker SDK if installed
        try:
            import sagemaker
            return sagemaker.image_uris.retrieve("xgboost", region, version="1.7-1")
        except Exception:
            print(f"ERROR: No XGBoost container mapped for region {region}.")
            print("Install 'sagemaker' SDK or add the region to the account_map.")
            sys.exit(1)
    return f"{account}.dkr.ecr.{region}.amazonaws.com/sagemaker-xgboost:1.7-1"


def start_training_job(sm_client, args, train_s3_uri):
    """Launch a SageMaker Training Job and wait for completion."""
    job_name = f"{TRAINING_JOB_PREFIX}-{int(time.time())}"
    image_uri = get_xgboost_image_uri(args.region)

    print(f"\nStarting training job: {job_name}")
    print(f"  Container: {image_uri}")
    print(f"  Instance:  {INSTANCE_TYPE_TRAIN}")
    print(f"  Hyperparameters: num_round={args.num_round}, max_depth={args.max_depth}, eta={args.eta}")

    sm_client.create_training_job(
        TrainingJobName=job_name,
        AlgorithmSpecification={
            "TrainingImage": image_uri,
            "TrainingInputMode": "File",
        },
        RoleArn=args.role_arn,
        InputDataConfig=[
            {
                "ChannelName": "train",
                "DataSource": {
                    "S3DataSource": {
                        "S3DataType": "S3Prefix",
                        "S3Uri": train_s3_uri,
                        "S3DataDistributionType": "FullyReplicated",
                    }
                },
                "ContentType": "text/csv",
            }
        ],
        OutputDataConfig={
            "S3OutputPath": f"s3://{args.bucket}/bloodbridge/output/"
        },
        ResourceConfig={
            "InstanceType": INSTANCE_TYPE_TRAIN,
            "InstanceCount": 1,
            "VolumeSizeInGB": 10,
        },
        StoppingCondition={
            "MaxRuntimeInSeconds": args.max_runtime,
        },
        HyperParameters={
            "objective": "binary:logistic",
            "num_round": str(args.num_round),
            "max_depth": str(args.max_depth),
            "eta": str(args.eta),
            "subsample": str(args.subsample),
            "min_child_weight": str(args.min_child_weight),
            "eval_metric": args.eval_metric,
            # Feature metadata
            "num_features": "18",
        },
    )

    # Poll for completion
    print("\nWaiting for training job to complete...")
    while True:
        desc = sm_client.describe_training_job(TrainingJobName=job_name)
        status = desc["TrainingJobStatus"]
        print(f"  Status: {status}", end="\r")
        if status in ("Completed", "Failed", "Stopped"):
            print(f"\n  Final status: {status}")
            break
        time.sleep(30)

    if status != "Completed":
        failure = desc.get("FailureReason", "Unknown")
        print(f"\n  ERROR: Training failed — {failure}")
        sys.exit(1)

    model_artefact = desc["ModelArtifacts"]["S3ModelArtifacts"]
    print(f"\n  Model artefact: {model_artefact}")
    return job_name, model_artefact


def deploy_endpoint(sm_client, args, model_artefact, job_name=None):
    """Create a SageMaker model + endpoint config + endpoint."""
    image_uri = get_xgboost_image_uri(args.region)
    model_name = f"bloodbridge-model-{int(time.time())}"
    endpoint_config_name = f"bloodbridge-epc-{int(time.time())}"

    # 1) Create Model
    print(f"\nCreating model: {model_name}")
    sm_client.create_model(
        ModelName=model_name,
        PrimaryContainer={
            "Image": image_uri,
            "ModelDataUrl": model_artefact,
        },
        ExecutionRoleArn=args.role_arn,
    )

    # 2) Create Endpoint Config
    print(f"Creating endpoint config: {endpoint_config_name}")
    sm_client.create_endpoint_config(
        EndpointConfigName=endpoint_config_name,
        ProductionVariants=[
            {
                "VariantName": "AllTraffic",
                "ModelName": model_name,
                "InitialInstanceCount": 1,
                "InstanceType": INSTANCE_TYPE_DEPLOY,
                "InitialVariantWeight": 1.0,
            }
        ],
    )

    # 3) Create or Update Endpoint
    endpoint_name = args.endpoint_name
    try:
        sm_client.describe_endpoint(EndpointName=endpoint_name)
        print(f"Updating existing endpoint: {endpoint_name}")
        sm_client.update_endpoint(
            EndpointName=endpoint_name,
            EndpointConfigName=endpoint_config_name,
        )
    except ClientError:
        print(f"Creating new endpoint: {endpoint_name}")
        sm_client.create_endpoint(
            EndpointName=endpoint_name,
            EndpointConfigName=endpoint_config_name,
        )

    # Wait for endpoint to be InService
    print("Waiting for endpoint to become InService...")
    while True:
        desc = sm_client.describe_endpoint(EndpointName=endpoint_name)
        status = desc["EndpointStatus"]
        print(f"  Endpoint status: {status}", end="\r")
        if status == "InService":
            print(f"\n  Endpoint is LIVE: {endpoint_name}")
            break
        elif status == "Failed":
            reason = desc.get("FailureReason", "Unknown")
            print(f"\n  ERROR: Endpoint creation failed — {reason}")
            sys.exit(1)
        time.sleep(30)

    print(f"\n{'='*60}")
    print(f"DEPLOYMENT COMPLETE")
    print(f"{'='*60}")
    print(f"Endpoint name : {endpoint_name}")
    print(f"Region        : {args.region}")
    print(f"Instance type : {INSTANCE_TYPE_DEPLOY}")
    print(f"\nAdd these to your .env:")
    print(f"  SAGEMAKER_ENDPOINT_ENABLED=true")
    print(f"  SAGEMAKER_ENDPOINT_NAME={endpoint_name}")
    print(f"  SAGEMAKER_REGION={args.region}")
    print(f"{'='*60}")


def main():
    args = parse_args()
    session = boto3.Session(region_name=args.region)
    s3_client = session.client("s3")
    sm_client = session.client("sagemaker")

    if args.deploy_only:
        if not args.model_artefact:
            print("ERROR: --model-artefact is required with --deploy-only")
            sys.exit(1)
        deploy_endpoint(sm_client, args, args.model_artefact)
        return

    # Upload training data
    if not os.path.exists(args.training_data):
        print(f"ERROR: Training data not found: {args.training_data}")
        print("Run:  python manage.py export_training_data --output training_data.csv --include-labels --no-header")
        sys.exit(1)

    train_s3_uri = upload_training_data(s3_client, args.bucket, args.training_data)

    # Train
    job_name, model_artefact = start_training_job(sm_client, args, train_s3_uri)

    if args.train_only:
        print(f"\nTraining complete. Model artefact: {model_artefact}")
        print(f"To deploy later, run:")
        print(f"  python {sys.argv[0]} --deploy-only --model-artefact {model_artefact} --bucket {args.bucket} --role-arn {args.role_arn}")
        return

    # Deploy
    deploy_endpoint(sm_client, args, model_artefact, job_name)


if __name__ == "__main__":
    main()
