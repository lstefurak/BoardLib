variable "aws_region" {
  description = "AWS region for the Lambda function."
  type        = string
  default     = "us-east-1"
}

variable "function_name" {
  description = "Lambda function name."
  type        = string
  default     = "boardlog-exporter"
}

variable "lambda_zip_path" {
  description = "Path to the packaged Lambda deployment zip."
  type        = string
  default     = "../../build/boardlog-lambda.zip"
}

variable "runtime" {
  description = "Lambda Python runtime."
  type        = string
  default     = "python3.13"
}

variable "architecture" {
  description = "Lambda CPU architecture. Keep this aligned with the package build."
  type        = string
  default     = "x86_64"
}

variable "allowed_origin" {
  description = "GitHub Pages origin allowed by Lambda Function URL CORS."
  type        = string
}

variable "access_key_param_name" {
  description = "SSM SecureString parameter name holding the backend access key (X-Board-Room-Key)."
  type        = string
  default     = "/boardlog/access-key"
}

variable "gate_phrase_param_name" {
  description = "SSM SecureString parameter name holding the page gate phrase (X-Board-Gate)."
  type        = string
  default     = "/boardlog/gate-phrase"
}

variable "allowed_boards" {
  description = "Comma-separated list of board names the backend will allow."
  type        = string
  default     = "tension"
}

variable "max_sync_pages" {
  description = "Maximum shared database sync pages per request."
  type        = number
  default     = 100
}

variable "timeout_seconds" {
  description = "Lambda timeout in seconds."
  type        = number
  default     = 60
}

variable "memory_size_mb" {
  description = "Lambda memory size in MB."
  type        = number
  default     = 1024
}

variable "reserved_concurrency" {
  description = "Max concurrent Lambda executions (caps cost/abuse on the public URL)."
  type        = number
  default     = 5
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days."
  type        = number
  default     = 14
}
