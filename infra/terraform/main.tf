terraform {
  required_version = ">= 1.3.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${var.function_name}-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_region" "current" {}

# The two SecureString parameters are created out-of-band (see infra README), so
# their plaintext values never enter terraform state or the repo. Terraform only
# grants the Lambda permission to read and decrypt them at runtime.
data "aws_kms_alias" "ssm" {
  name = "alias/aws/ssm"
}

data "aws_iam_policy_document" "lambda_secrets" {
  statement {
    sid     = "ReadBoardlogSecrets"
    actions = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = [
      "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter${var.access_key_param_name}",
      "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter${var.gate_phrase_param_name}",
    ]
  }

  statement {
    sid       = "DecryptBoardlogSecrets"
    actions   = ["kms:Decrypt"]
    resources = [data.aws_kms_alias.ssm.target_key_arn]
  }
}

resource "aws_iam_role_policy" "lambda_secrets" {
  name   = "${var.function_name}-secrets-read"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda_secrets.json
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${var.function_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "boardlog" {
  function_name    = var.function_name
  description      = "BoardLog Tension logbook JSON exporter for the GitHub Pages visualizer"
  role             = aws_iam_role.lambda.arn
  handler          = "backend.boardlog_lambda.handler.lambda_handler"
  runtime          = var.runtime
  architectures    = [var.architecture]
  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)
  timeout          = var.timeout_seconds
  memory_size      = var.memory_size_mb

  # Caps concurrent executions so a flood against the public URL can't run up an
  # unbounded bill or exhaust account concurrency.
  reserved_concurrent_executions = var.reserved_concurrency

  environment {
    variables = {
      # CORS (allowed origin) is configured on the Function URL below, not read
      # by the handler — so ALLOWED_ORIGIN is intentionally not passed here.
      BOARDLOG_ACCESS_KEY_PARAM  = var.access_key_param_name
      BOARDLOG_GATE_PHRASE_PARAM = var.gate_phrase_param_name
      BOARDLOG_ALLOWED_BOARDS    = var.allowed_boards
      BOARDLOG_MAX_SYNC_PAGES    = tostring(var.max_sync_pages)
      BOARDLOG_CACHE_DIR         = "/tmp/boardlog"
      PYTHONPATH                 = "/var/task"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.lambda,
    aws_iam_role_policy_attachment.lambda_basic,
  ]
}

resource "aws_lambda_function_url" "boardlog" {
  function_name      = aws_lambda_function.boardlog.function_name
  authorization_type = "NONE"

  cors {
    allow_credentials = false
    allow_headers     = ["content-type", "x-board-room-key", "x-board-gate"]
    allow_methods     = ["POST"]
    allow_origins     = [var.allowed_origin]
    max_age           = 300
  }
}

# A public Function URL (authorization_type = NONE) requires an explicit
# resource policy granting lambda:InvokeFunctionUrl to principal "*". The page
# is open to anyone, but the handler still requires the gate phrase AND access
# key (verified server-side) before doing any work.
#
# NOTE: changes to the URL auth type or this permission can take a few minutes
# to propagate to the Lambda edge. A transient 403 right after an apply is
# usually propagation, not a misconfiguration.
resource "aws_lambda_permission" "function_url_public_invoke" {
  statement_id           = "AllowPublicFunctionUrlInvoke"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.boardlog.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}
