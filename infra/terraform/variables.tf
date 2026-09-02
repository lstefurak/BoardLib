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

variable "session_ttl_seconds" {
  description = "Lifetime of the session token a correct gate phrase earns (the page's login). Rotating either secret revokes all sessions early."
  type        = number
  default     = 43200
}

variable "instagram_staging_bucket" {
  description = "Name of the private S3 bucket the Instagram uploader stages clips in. Empty = boardlog-instagram-staging-<account id>."
  type        = string
  default     = ""
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

variable "alarm_actions" {
  description = "ARNs (e.g. an SNS topic) notified when a monitoring alarm fires. Empty list disables notifications but keeps the alarms visible in the console."
  type        = list(string)
  default     = []
}

variable "auth_failure_alarm_threshold" {
  description = "403s per 5-minute period (sustained for 3 periods) that trigger the auth-failures alarm."
  type        = number
  default     = 10
}

variable "server_error_alarm_threshold" {
  description = "502s per 5-minute period (sustained for 2 periods) that trigger the server-errors alarm."
  type        = number
  default     = 3
}
