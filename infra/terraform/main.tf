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

  environment {
    variables = {
      ALLOWED_ORIGIN          = var.allowed_origin
      BOARDLOG_ACCESS_KEY     = var.boardlog_access_key
      BOARDLOG_ALLOWED_BOARDS = var.allowed_boards
      BOARDLOG_MAX_SYNC_PAGES = tostring(var.max_sync_pages)
      BOARDLOG_CACHE_DIR      = "/tmp/boardlog"
      PYTHONPATH              = "/var/task"
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
    allow_headers     = ["content-type", "x-board-room-key"]
    allow_methods     = ["POST"]
    allow_origins     = [var.allowed_origin]
    max_age           = 300
  }
}
