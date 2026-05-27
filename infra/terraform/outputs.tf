output "function_url" {
  description = "Lambda Function URL to put in docs/site.config.js."
  value       = aws_lambda_function_url.boardlog.function_url
}

output "function_name" {
  description = "Lambda function name."
  value       = aws_lambda_function.boardlog.function_name
}
