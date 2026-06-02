output "function_url" {
  description = "Lambda Function URL to put in docs/site.config.js."
  value       = aws_lambda_function_url.boardlog.function_url
}

output "function_name" {
  description = "Lambda function name."
  value       = aws_lambda_function.boardlog.function_name
}

output "dashboard_url" {
  description = "CloudWatch dashboard for invocations, success rate, and per-username usage."
  value       = "https://${data.aws_region.current.name}.console.aws.amazon.com/cloudwatch/home?region=${data.aws_region.current.name}#dashboards:name=${aws_cloudwatch_dashboard.boardlog.dashboard_name}"
}
