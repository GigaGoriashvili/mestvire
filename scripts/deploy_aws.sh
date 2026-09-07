#!/usr/bin/env bash
set -eo pipefail

REGION="eu-central-1"
AWS_PROFILE_ARG=""

# Parse optional arguments: -p/--profile <profile>, -r/--region <region>
while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--profile)
      AWS_PROFILE_ARG="$2"
      shift 2
      ;;
    -r|--region)
      REGION="$2"
      shift 2
      ;;
    *)
      if [ -z "$AWS_PROFILE_ARG" ]; then
        AWS_PROFILE_ARG="$1"
      elif [ -z "$REGION" ]; then
        REGION="$1"
      fi
      shift
      ;;
  esac
done

if [ -n "$AWS_PROFILE_ARG" ]; then
  export AWS_PROFILE="$AWS_PROFILE_ARG"
fi

# Dynamically resolve AWS Profile if not set and exactly 1 profile exists
if [ -z "$AWS_PROFILE" ]; then
  PROFILES=($(aws configure list-profiles 2>/dev/null || true))
  if [ ${#PROFILES[@]} -eq 1 ]; then
    export AWS_PROFILE="${PROFILES[0]}"
  fi
fi

if [ -n "$AWS_PROFILE" ]; then
  echo "Active AWS Profile: $AWS_PROFILE"
fi

echo "Checking AWS caller identity..."
if ! ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>&1); then
  echo "Error: Failed to get AWS caller identity: $ACCOUNT_ID" >&2
  PROFILES=($(aws configure list-profiles 2>/dev/null || true))
  if [ ${#PROFILES[@]} -gt 0 ]; then
    echo "Available profiles: ${PROFILES[*]}" >&2
  fi
  echo "Please specify profile via: ./scripts/deploy_aws.sh --profile <name> or export AWS_PROFILE=<name>" >&2
  exit 1
fi
ACCOUNT_ID=$(echo "$ACCOUNT_ID" | tr -d '[:space:]')

ROLE_NAME="JobsScraperLambdaRole"
FUNCTION_NAME="jobs-tracker-scraper"
LAMBDA_ARN="arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:${FUNCTION_NAME}"

echo "=========================================="
echo "Mestvire AWS Infrastructure Deployment (Bash)"
echo "Region:     $REGION"
echo "Account ID: $ACCOUNT_ID"
echo "=========================================="

# ==========================================
# 1. DynamoDB Table & TTL
# ==========================================
echo "1. Creating DynamoDB table 'jobs_tracker'..."
OUT=$(aws dynamodb create-table \
    --table-name jobs_tracker \
    --attribute-definitions \
        AttributeName=source,AttributeType=S \
        AttributeName=job_id,AttributeType=S \
    --key-schema \
        AttributeName=source,KeyType=HASH \
        AttributeName=job_id,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST \
    --region "$REGION" 2>&1) || {
    if echo "$OUT" | grep -q "ResourceInUseException"; then
        echo "DynamoDB table 'jobs_tracker' already exists, continuing..."
    else
        echo "$OUT"
    fi
}

echo "Waiting for DynamoDB table to become ACTIVE..."
aws dynamodb wait table-exists --table-name jobs_tracker --region "$REGION"

echo "Configuring TTL on 'expire_at' (+30 days)..."
OUT=$(aws dynamodb update-time-to-live \
    --table-name jobs_tracker \
    --time-to-live-specification "Enabled=true, AttributeName=expire_at" \
    --region "$REGION" 2>&1) || {
    if echo "$OUT" | grep -q "ValidationException"; then
        echo "TTL on 'expire_at' already enabled, continuing..."
    else
        echo "$OUT"
    fi
}

# ==========================================
# 2. SSM Parameter Store
# ==========================================
echo "2. Storing secrets in SSM Parameter Store (/jobs/)..."
BOT_TOKEN="YOUR_BOT_TOKEN"
CHAT_ID="YOUR_CHAT_ID"
GEMINI_KEY="YOUR_GEMINI_KEY"

if [ -f ".env" ]; then
    echo "Loading secrets from local .env..."
    while IFS='=' read -r key val || [ -n "$key" ]; do
        # Trim leading/trailing whitespace
        key=$(echo "$key" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
        # Strip comments and surrounding quotes
        val=$(echo "$val" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^["'"'"']\(.*\)["'"'"']$/\1/')
        case "$key" in
            TELEGRAM_BOT_TOKEN) BOT_TOKEN="$val" ;;
            TELEGRAM_CHAT_ID)   CHAT_ID="$val" ;;
            GEMINI_API_KEY)     GEMINI_KEY="$val" ;;
        esac
    done < .env
fi

aws ssm put-parameter --name "/jobs/telegram_bot_token" --value "$BOT_TOKEN" --type "SecureString" --overwrite --region "$REGION"
aws ssm put-parameter --name "/jobs/telegram_chat_id" --value "$CHAT_ID" --type "String" --overwrite --region "$REGION"
aws ssm put-parameter --name "/jobs/gemini_api_key" --value "$GEMINI_KEY" --type "SecureString" --overwrite --region "$REGION"

# ==========================================
# 3. IAM Execution Role & Policies
# ==========================================
echo "3. Creating IAM Role '$ROLE_NAME'..."
TRUST_FILE=$(mktemp)
cat <<'EOF' > "$TRUST_FILE"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

OUT=$(aws iam create-role --role-name "$ROLE_NAME" --assume-role-policy-document "file://$TRUST_FILE" 2>&1) || {
    if echo "$OUT" | grep -q "EntityAlreadyExists"; then
        echo "IAM Role '$ROLE_NAME' already exists, continuing..."
    else
        echo "$OUT"
    fi
}
rm -f "$TRUST_FILE"

aws iam attach-role-policy --role-name "$ROLE_NAME" --policy-arn "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"

INLINE_FILE=$(mktemp)
cat <<EOF > "$INLINE_FILE"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DynamoDBAccess",
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:DescribeTable"
      ],
      "Resource": "arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/jobs_tracker"
    },
    {
      "Sid": "SSMParameterAccess",
      "Effect": "Allow",
      "Action": [
        "ssm:GetParameters",
        "ssm:GetParameter",
        "ssm:GetParametersByPath"
      ],
      "Resource": "arn:aws:ssm:${REGION}:${ACCOUNT_ID}:parameter/jobs/*"
    },
    {
      "Sid": "KMSDecryptAccess",
      "Effect": "Allow",
      "Action": [
        "kms:Decrypt"
      ],
      "Resource": "*"
    }
  ]
}
EOF

aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name "JobsScraperPolicy" --policy-document "file://$INLINE_FILE"
rm -f "$INLINE_FILE"

echo "Waiting 10 seconds for IAM Role propagation..."
sleep 10

# ==========================================
# 4. Lambda Function Initialization
# ==========================================
echo "4. Deploying Lambda Function '$FUNCTION_NAME'..."

if [ -f "deployment_package.zip" ]; then
    echo "Found existing deployment_package.zip! Deploying directly..."
    CREATED=false
    for attempt in {1..6}; do
        OUT=$(aws lambda create-function \
            --function-name "$FUNCTION_NAME" \
            --runtime python3.12 \
            --role "arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}" \
            --handler lambda_function.lambda_handler \
            --timeout 300 \
            --memory-size 512 \
            --zip-file fileb://deployment_package.zip \
            --environment "Variables={DYNAMODB_TABLE_NAME=jobs_tracker,SSM_PREFIX=/jobs,FILTER_SENIOR_ROLES=true}" \
            --region "$REGION" 2>&1) && CREATED=true && break || true

        if echo "$OUT" | grep -qE "(cannot be assumed by Lambda|The role defined for the function cannot be assumed)"; then
            echo "Waiting for IAM role propagation to Lambda (attempt $attempt/6)..."
            sleep 8
        elif echo "$OUT" | grep -q "ResourceConflictException"; then
            echo "Lambda function already exists, updating code and configuration..."
            aws lambda update-function-code \
                --function-name "$FUNCTION_NAME" \
                --zip-file fileb://deployment_package.zip \
                --region "$REGION" >/dev/null
            aws lambda update-function-configuration \
                --function-name "$FUNCTION_NAME" \
                --timeout 300 \
                --memory-size 512 \
                --environment "Variables={DYNAMODB_TABLE_NAME=jobs_tracker,SSM_PREFIX=/jobs,FILTER_SENIOR_ROLES=true}" \
                --region "$REGION" >/dev/null
            CREATED=true
            break
        else
            echo "$OUT"
            break
        fi
    done

    if [ "$CREATED" != "true" ]; then
        echo "Updating code for existing function..."
        aws lambda update-function-code \
            --function-name "$FUNCTION_NAME" \
            --zip-file fileb://deployment_package.zip \
            --region "$REGION" >/dev/null
        aws lambda update-function-configuration \
            --function-name "$FUNCTION_NAME" \
            --timeout 300 \
            --memory-size 512 \
            --environment "Variables={DYNAMODB_TABLE_NAME=jobs_tracker,SSM_PREFIX=/jobs,FILTER_SENIOR_ROLES=true}" \
            --region "$REGION" >/dev/null
    fi
else
    echo "deployment_package.zip not found. Please run 'python scripts/package_lambda.py' first."
    exit 1
fi

echo "Setting CloudWatch Log Retention to 14 days..."
OUT=$(aws logs create-log-group --log-group-name "/aws/lambda/${FUNCTION_NAME}" --region "$REGION" 2>&1) || {
    if ! echo "$OUT" | grep -q "ResourceAlreadyExistsException"; then
        echo "$OUT"
    fi
}

aws logs put-retention-policy \
    --log-group-name "/aws/lambda/${FUNCTION_NAME}" \
    --retention-in-days 14 \
    --region "$REGION"

# ==========================================
# 5. EventBridge Schedules & Permissions
# ==========================================
echo "5. Configuring EventBridge Schedules..."

TARGETS_FILE=$(mktemp)
cat <<EOF > "$TARGETS_FILE"
[
  {
    "Id": "1",
    "Arn": "$LAMBDA_ARN",
    "Input": "{\"source\":\"all\"}"
  }
]
EOF

# A. Weekdays: Hourly between 10:00 and 22:00 Tbilisi Time (06:00 - 18:00 UTC)
aws events put-rule \
    --name "jobs-tracker-weekdays" \
    --schedule-expression "cron(0 6-18 ? * MON-FRI *)" \
    --region "$REGION"

aws events put-targets \
    --rule "jobs-tracker-weekdays" \
    --targets "file://$TARGETS_FILE" \
    --region "$REGION"

OUT=$(aws lambda add-permission \
    --function-name "$FUNCTION_NAME" \
    --statement-id "EventBridgeInvokeWeekdays" \
    --action "lambda:InvokeFunction" \
    --principal "events.amazonaws.com" \
    --source-arn "arn:aws:events:${REGION}:${ACCOUNT_ID}:rule/jobs-tracker-weekdays" \
    --region "$REGION" 2>&1) || {
    if ! echo "$OUT" | grep -q "ResourceConflictException"; then
        echo "$OUT"
    fi
}

# B. Weekends: Once daily at 12:00 Tbilisi Time (08:00 UTC)
aws events put-rule \
    --name "jobs-tracker-weekends" \
    --schedule-expression "cron(0 8 ? * SAT-SUN *)" \
    --region "$REGION"

aws events put-targets \
    --rule "jobs-tracker-weekends" \
    --targets "file://$TARGETS_FILE" \
    --region "$REGION"

OUT=$(aws lambda add-permission \
    --function-name "$FUNCTION_NAME" \
    --statement-id "EventBridgeInvokeWeekends" \
    --action "lambda:InvokeFunction" \
    --principal "events.amazonaws.com" \
    --source-arn "arn:aws:events:${REGION}:${ACCOUNT_ID}:rule/jobs-tracker-weekends" \
    --region "$REGION" 2>&1) || {
    if ! echo "$OUT" | grep -q "ResourceConflictException"; then
        echo "$OUT"
    fi
}

rm -f "$TARGETS_FILE"

echo "=========================================="
echo "DEPLOYMENT COMPLETED SUCCESSFULLY!"
echo "=========================================="
