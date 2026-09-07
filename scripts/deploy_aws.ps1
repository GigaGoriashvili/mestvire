param(
    [string]$AwsProfile,
    [string]$Region = "eu-central-1"
)

$ErrorActionPreference = "Continue"
$REGION = $Region

# Find aws executable
$AWS_CMD = "aws"
if (-not (Get-Command "aws" -ErrorAction SilentlyContinue)) {
    if (Test-Path "C:\Program Files\Amazon\AWSCLIV2\aws.exe") {
        $AWS_CMD = "C:\Program Files\Amazon\AWSCLIV2\aws.exe"
        $env:Path = "C:\Program Files\Amazon\AWSCLIV2;" + $env:Path
    } else {
        Write-Error "AWS CLI is not found in PATH or standard installation directory."
        exit 1
    }
}

# Resolve AWS Profile dynamically without hardcoding
$profileList = @()
try {
    foreach ($p in (& $AWS_CMD configure list-profiles 2>$null)) {
        $trimmed = "$p".Trim()
        if ($trimmed) { $profileList += $trimmed }
    }
} catch {}

if ($AwsProfile) {
    $env:AWS_PROFILE = $AwsProfile
} elseif ($env:AWS_PROFILE -and ($profileList.Count -gt 0) -and ($profileList -notcontains $env:AWS_PROFILE)) {
    # If the session has a stale or invalid profile, reset it
    Remove-Item env:AWS_PROFILE -ErrorAction SilentlyContinue
}

if (-not $env:AWS_PROFILE -and $profileList.Count -eq 1) {
    $env:AWS_PROFILE = $profileList[0]
}

if ($env:AWS_PROFILE) {
    Write-Host "Active AWS Profile: $env:AWS_PROFILE" -ForegroundColor Cyan
}

Write-Host "Checking AWS caller identity..."
$ACCOUNT_ID = (& $AWS_CMD sts get-caller-identity --query Account --output text 2>&1)
if ($LASTEXITCODE -ne 0 -or -not "$ACCOUNT_ID".Trim()) {
    $available = ""
    if ($profileList.Count -gt 0) {
        $available = " Available profiles: " + ($profileList -join ", ")
    }
    Write-Error "Failed to get AWS caller identity: $ACCOUNT_ID.$available`nPlease specify profile via: .\scripts\deploy_aws.ps1 -AwsProfile <name> or set `$env:AWS_PROFILE."
    exit 1
}
$ACCOUNT_ID = "$ACCOUNT_ID".Trim()

$ROLE_NAME = "JobsScraperLambdaRole"
$FUNCTION_NAME = "jobs-tracker-scraper"
$LAMBDA_ARN = "arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:${FUNCTION_NAME}"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Mestvire AWS Infrastructure Deployment (PowerShell)" -ForegroundColor Cyan
Write-Host "Region:     $REGION"
Write-Host "Account ID: $ACCOUNT_ID"
Write-Host "==========================================" -ForegroundColor Cyan

# 1. DynamoDB Table & TTL
Write-Host "1. Creating DynamoDB table 'jobs_tracker'..." -ForegroundColor Yellow
$out = & $AWS_CMD dynamodb create-table `
    --table-name jobs_tracker `
    --attribute-definitions AttributeName=source,AttributeType=S AttributeName=job_id,AttributeType=S `
    --key-schema AttributeName=source,KeyType=HASH AttributeName=job_id,KeyType=RANGE `
    --billing-mode PAY_PER_REQUEST `
    --region $REGION 2>&1
if ($LASTEXITCODE -ne 0 -and "$out" -match "ResourceInUseException") {
    Write-Host "DynamoDB table 'jobs_tracker' already exists, continuing..."
} elseif ($LASTEXITCODE -ne 0) {
    Write-Host "$out"
}

Write-Host "Waiting for DynamoDB table to become ACTIVE..."
& $AWS_CMD dynamodb wait table-exists --table-name jobs_tracker --region $REGION

Write-Host "Configuring TTL on 'expire_at' (+30 days)..."
$out = & $AWS_CMD dynamodb update-time-to-live `
    --table-name jobs_tracker `
    --time-to-live-specification "Enabled=true, AttributeName=expire_at" `
    --region $REGION 2>&1
if ($LASTEXITCODE -ne 0 -and "$out" -match "ValidationException") {
    Write-Host "TTL on 'expire_at' already enabled, continuing..."
} elseif ($LASTEXITCODE -ne 0) {
    Write-Host "$out"
}

# 2. SSM Parameter Store
Write-Host "2. Storing secrets in SSM Parameter Store (/jobs/)..." -ForegroundColor Yellow

$BOT_TOKEN = "YOUR_BOT_TOKEN"
$CHAT_ID = "YOUR_CHAT_ID"
$GEMINI_KEY = "YOUR_GEMINI_KEY"

if (Test-Path ".env") {
    Write-Host "Loading secrets from local .env..."
    Get-Content ".env" | ForEach-Object {
        if ($_ -match '^\s*TELEGRAM_BOT_TOKEN\s*=\s*["'']?(.*?)["'']?\s*$') { $BOT_TOKEN = $matches[1] }
        if ($_ -match '^\s*TELEGRAM_CHAT_ID\s*=\s*["'']?(.*?)["'']?\s*$') { $CHAT_ID = $matches[1] }
        if ($_ -match '^\s*GEMINI_API_KEY\s*=\s*["'']?(.*?)["'']?\s*$') { $GEMINI_KEY = $matches[1] }
    }
}

& $AWS_CMD ssm put-parameter --name "/jobs/telegram_bot_token" --value $BOT_TOKEN --type "SecureString" --overwrite --region $REGION
& $AWS_CMD ssm put-parameter --name "/jobs/telegram_chat_id" --value $CHAT_ID --type "String" --overwrite --region $REGION
& $AWS_CMD ssm put-parameter --name "/jobs/gemini_api_key" --value $GEMINI_KEY --type "SecureString" --overwrite --region $REGION

# 3. IAM Execution Role & Policies
Write-Host "3. Creating IAM Role '$ROLE_NAME'..." -ForegroundColor Yellow

$trustFile = [System.IO.Path]::GetTempFileName()
$TRUST_POLICY = @'
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
'@
Set-Content -Path $trustFile -Value $TRUST_POLICY -Encoding Ascii
$trustFileUri = "file://" + $trustFile.Replace('\', '/')

$out = & $AWS_CMD iam create-role --role-name $ROLE_NAME --assume-role-policy-document "$trustFileUri" 2>&1
if ($LASTEXITCODE -ne 0 -and "$out" -match "EntityAlreadyExists") {
    Write-Host "IAM Role '$ROLE_NAME' already exists, continuing..."
} elseif ($LASTEXITCODE -ne 0) {
    Write-Host "$out"
}
Remove-Item $trustFile -ErrorAction SilentlyContinue

& $AWS_CMD iam attach-role-policy --role-name $ROLE_NAME --policy-arn "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"

$inlineFile = [System.IO.Path]::GetTempFileName()
$INLINE_POLICY = @"
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
"@
Set-Content -Path $inlineFile -Value $INLINE_POLICY -Encoding Ascii
$inlineFileUri = "file://" + $inlineFile.Replace('\', '/')

& $AWS_CMD iam put-role-policy --role-name $ROLE_NAME --policy-name "JobsScraperPolicy" --policy-document "$inlineFileUri"
Remove-Item $inlineFile -ErrorAction SilentlyContinue

Write-Host "Waiting 10 seconds for IAM Role propagation..."
Start-Sleep -Seconds 10

# 4. Lambda Function Initialization
Write-Host "4. Deploying Lambda Function '$FUNCTION_NAME'..." -ForegroundColor Yellow

if (Test-Path "deployment_package.zip") {
    Write-Host "Found existing deployment_package.zip! Deploying directly..."
    $zipPath = (Resolve-Path "deployment_package.zip").Path.Replace('\', '/')
    $created = $false
    for ($attempt = 1; $attempt -le 6; $attempt++) {
        $out = & $AWS_CMD lambda create-function `
            --function-name $FUNCTION_NAME `
            --runtime python3.12 `
            --role "arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}" `
            --handler lambda_function.lambda_handler `
            --timeout 300 `
            --memory-size 512 `
            --zip-file "fileb://$zipPath" `
            --environment "Variables={DYNAMODB_TABLE_NAME=jobs_tracker,SSM_PREFIX=/jobs,FILTER_SENIOR_ROLES=true}" `
            --region $REGION 2>&1

        if ($LASTEXITCODE -eq 0) {
            $created = $true
            Write-Host "Lambda function '$FUNCTION_NAME' created successfully."
            break
        }

        if ("$out" -match "cannot be assumed by Lambda" -or "$out" -match "The role defined for the function cannot be assumed") {
            Write-Host "Waiting for IAM role propagation to Lambda (attempt $attempt/6)..."
            Start-Sleep -Seconds 8
        } elseif ("$out" -match "ResourceConflictException") {
            Write-Host "Lambda function already exists, updating code and configuration..."
            & $AWS_CMD lambda update-function-code --function-name $FUNCTION_NAME --zip-file "fileb://$zipPath" --region $REGION
            & $AWS_CMD lambda update-function-configuration `
                --function-name $FUNCTION_NAME `
                --timeout 300 `
                --memory-size 512 `
                --environment "Variables={DYNAMODB_TABLE_NAME=jobs_tracker,SSM_PREFIX=/jobs,FILTER_SENIOR_ROLES=true}" `
                --region $REGION
            $created = $true
            break
        } else {
            Write-Host "$out"
            break
        }
    }
    if (-not $created) {
        Write-Host "Updating code for existing function..."
        & $AWS_CMD lambda update-function-code --function-name $FUNCTION_NAME --zip-file "fileb://$zipPath" --region $REGION
        & $AWS_CMD lambda update-function-configuration `
            --function-name $FUNCTION_NAME `
            --timeout 300 `
            --memory-size 512 `
            --environment "Variables={DYNAMODB_TABLE_NAME=jobs_tracker,SSM_PREFIX=/jobs,FILTER_SENIOR_ROLES=true}" `
            --region $REGION
    }
} else {
    Write-Host "deployment_package.zip not found. Please run 'python scripts/package_lambda.py' first."
    exit 1
}

Write-Host "Setting CloudWatch Log Retention to 14 days..."
$out = & $AWS_CMD logs create-log-group --log-group-name "/aws/lambda/${FUNCTION_NAME}" --region $REGION 2>&1
if ($LASTEXITCODE -ne 0 -and "$out" -match "ResourceAlreadyExistsException") {
    # Log group already exists
} elseif ($LASTEXITCODE -ne 0) {
    Write-Host "$out"
}

& $AWS_CMD logs put-retention-policy --log-group-name "/aws/lambda/${FUNCTION_NAME}" --retention-in-days 14 --region $REGION

# 5. EventBridge Schedules & Permissions
Write-Host "5. Configuring EventBridge Schedules..." -ForegroundColor Yellow

$targetsFile = [System.IO.Path]::GetTempFileName()
$TARGETS_JSON = @"
[
  {
    "Id": "1",
    "Arn": "$LAMBDA_ARN",
    "Input": "{\"source\":\"all\"}"
  }
]
"@
Set-Content -Path $targetsFile -Value $TARGETS_JSON -Encoding Ascii
$targetsFileUri = "file://" + $targetsFile.Replace('\', '/')

# Weekdays: Hourly between 10:00 and 22:00 Tbilisi Time (06:00 - 18:00 UTC)
& $AWS_CMD events put-rule --name "jobs-tracker-weekdays" --schedule-expression "cron(0 6-18 ? * MON-FRI *)" --region $REGION
& $AWS_CMD events put-targets --rule "jobs-tracker-weekdays" --targets "$targetsFileUri" --region $REGION
$out = & $AWS_CMD lambda add-permission --function-name $FUNCTION_NAME --statement-id "EventBridgeInvokeWeekdays" --action "lambda:InvokeFunction" --principal "events.amazonaws.com" --source-arn "arn:aws:events:${REGION}:${ACCOUNT_ID}:rule/jobs-tracker-weekdays" --region $REGION 2>&1
if ($LASTEXITCODE -ne 0 -and "$out" -match "ResourceConflictException") {
    # Permission already exists
} elseif ($LASTEXITCODE -ne 0) {
    Write-Host "$out"
}

# Weekends: Once daily at 12:00 Tbilisi Time (08:00 UTC)
& $AWS_CMD events put-rule --name "jobs-tracker-weekends" --schedule-expression "cron(0 8 ? * SAT-SUN *)" --region $REGION
& $AWS_CMD events put-targets --rule "jobs-tracker-weekends" --targets "$targetsFileUri" --region $REGION
$out = & $AWS_CMD lambda add-permission --function-name $FUNCTION_NAME --statement-id "EventBridgeInvokeWeekends" --action "lambda:InvokeFunction" --principal "events.amazonaws.com" --source-arn "arn:aws:events:${REGION}:${ACCOUNT_ID}:rule/jobs-tracker-weekends" --region $REGION 2>&1
if ($LASTEXITCODE -ne 0 -and "$out" -match "ResourceConflictException") {
    # Permission already exists
} elseif ($LASTEXITCODE -ne 0) {
    Write-Host "$out"
}

Remove-Item $targetsFile -ErrorAction SilentlyContinue

Write-Host "==========================================" -ForegroundColor Green
Write-Host "DEPLOYMENT COMPLETED SUCCESSFULLY!" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green
