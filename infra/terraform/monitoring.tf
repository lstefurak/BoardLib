# Observability for the BoardLog Lambda.
#
# The handler emits one structured JSON line per request (type =
# "boardlog_request") carrying the outcome status, the action, and the board
# username (never the password or the gate/access secrets). The metric filters
# below turn those lines into CloudWatch metrics, and the dashboard renders both
# those metrics and the raw lines (broken down by username) for debugging.

locals {
  metrics_namespace = "BoardLog"
}

# --- Log metric filters ---------------------------------------------------

resource "aws_cloudwatch_log_metric_filter" "export_requests" {
  name           = "${var.function_name}-export-requests"
  log_group_name = aws_cloudwatch_log_group.lambda.name
  pattern        = "{ $.type = \"boardlog_request\" && $.action = \"export\" }"

  metric_transformation {
    name          = "ExportRequests"
    namespace     = local.metrics_namespace
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

resource "aws_cloudwatch_log_metric_filter" "export_success" {
  name           = "${var.function_name}-export-success"
  log_group_name = aws_cloudwatch_log_group.lambda.name
  pattern        = "{ $.type = \"boardlog_request\" && $.action = \"export\" && $.status = 200 }"

  metric_transformation {
    name          = "ExportSuccess"
    namespace     = local.metrics_namespace
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

resource "aws_cloudwatch_log_metric_filter" "auth_failures" {
  name           = "${var.function_name}-auth-failures"
  log_group_name = aws_cloudwatch_log_group.lambda.name
  pattern        = "{ $.type = \"boardlog_request\" && $.status = 403 }"

  metric_transformation {
    name          = "AuthFailures"
    namespace     = local.metrics_namespace
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

resource "aws_cloudwatch_log_metric_filter" "server_errors" {
  name           = "${var.function_name}-server-errors"
  log_group_name = aws_cloudwatch_log_group.lambda.name
  pattern        = "{ $.type = \"boardlog_request\" && $.status = 502 }"

  metric_transformation {
    name          = "ServerErrors"
    namespace     = local.metrics_namespace
    value         = "1"
    default_value = "0"
    unit          = "Count"
  }
}

# --- Dashboard ------------------------------------------------------------

resource "aws_cloudwatch_dashboard" "boardlog" {
  dashboard_name = "${var.function_name}-overview"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "text"
        x      = 0
        y      = 0
        width  = 24
        height = 2
        properties = {
          markdown = "# BoardLog — ${var.function_name}\nExport success rate, request outcomes, and invocations by username. Source log group: `${aws_cloudwatch_log_group.lambda.name}`."
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 2
        width  = 6
        height = 6
        properties = {
          title  = "Export success rate"
          view   = "gauge"
          region = data.aws_region.current.name
          period = 86400
          yAxis  = { left = { min = 0, max = 100 } }
          metrics = [
            [local.metrics_namespace, "ExportSuccess", { id = "s", stat = "Sum", visible = false }],
            [local.metrics_namespace, "ExportRequests", { id = "r", stat = "Sum", visible = false }],
            [{ expression = "100 * s / r", label = "Success %", id = "e" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 6
        y      = 2
        width  = 9
        height = 6
        properties = {
          title   = "Export requests vs successes"
          view    = "timeSeries"
          stacked = false
          region  = data.aws_region.current.name
          period  = 300
          metrics = [
            [local.metrics_namespace, "ExportRequests", { stat = "Sum", label = "Requests" }],
            [local.metrics_namespace, "ExportSuccess", { stat = "Sum", label = "Successes" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 15
        y      = 2
        width  = 9
        height = 6
        properties = {
          title   = "Rejections & errors"
          view    = "timeSeries"
          stacked = false
          region  = data.aws_region.current.name
          period  = 300
          metrics = [
            [local.metrics_namespace, "AuthFailures", { stat = "Sum", label = "403 auth failures" }],
            [local.metrics_namespace, "ServerErrors", { stat = "Sum", label = "502 errors" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 8
        width  = 12
        height = 6
        properties = {
          title   = "Lambda invocations / errors / throttles"
          view    = "timeSeries"
          stacked = false
          region  = data.aws_region.current.name
          period  = 300
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", var.function_name, { stat = "Sum" }],
            ["AWS/Lambda", "Errors", "FunctionName", var.function_name, { stat = "Sum" }],
            ["AWS/Lambda", "Throttles", "FunctionName", var.function_name, { stat = "Sum" }],
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 8
        width  = 12
        height = 6
        properties = {
          title   = "Duration (p50 / p99)"
          view    = "timeSeries"
          stacked = false
          region  = data.aws_region.current.name
          period  = 300
          metrics = [
            ["AWS/Lambda", "Duration", "FunctionName", var.function_name, { stat = "p50", label = "p50" }],
            ["AWS/Lambda", "Duration", "FunctionName", var.function_name, { stat = "p99", label = "p99" }],
          ]
        }
      },
      {
        type   = "log"
        x      = 0
        y      = 14
        width  = 12
        height = 8
        properties = {
          title  = "Invocations by username"
          region = data.aws_region.current.name
          view   = "table"
          query  = "SOURCE '${aws_cloudwatch_log_group.lambda.name}' | filter type = \"boardlog_request\" and ispresent(username) | stats count(*) as requests, sum(status = 200) as successes, latest(@timestamp) as last_seen by username, board | sort requests desc"
        }
      },
      {
        type   = "log"
        x      = 12
        y      = 14
        width  = 12
        height = 8
        properties = {
          title  = "Recent export attempts"
          region = data.aws_region.current.name
          view   = "table"
          query  = "SOURCE '${aws_cloudwatch_log_group.lambda.name}' | filter type = \"boardlog_request\" and action = \"export\" | sort @timestamp desc | limit 50 | display @timestamp, username, board, status, row_count"
        }
      },
    ]
  })
}

# --- Alarms ----------------------------------------------------------------
# The metric filters above are only useful if something watches them. These
# alarms notify on the two abuse signals that matter for a public Function URL:
# sustained auth failures (credential stuffing / probing) and any throttling
# (the reserved-concurrency cap locking out the legitimate user). Set
# var.alarm_actions to an SNS topic ARN to get notified; without it the alarms
# still show their state in the console.

resource "aws_cloudwatch_metric_alarm" "auth_failures" {
  alarm_name          = "${var.function_name}-auth-failures"
  alarm_description   = "Sustained 403s against the public BoardLog endpoint (possible credential stuffing)."
  namespace           = local.metrics_namespace
  metric_name         = "AuthFailures"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 3
  threshold           = var.auth_failure_alarm_threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_actions
  ok_actions          = var.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "throttles" {
  alarm_name          = "${var.function_name}-throttles"
  alarm_description   = "Lambda throttling: the reserved-concurrency cap is being hit, locking out legitimate use."
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  dimensions          = { FunctionName = var.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_actions
  ok_actions          = var.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "server_errors" {
  alarm_name          = "${var.function_name}-server-errors"
  alarm_description   = "Repeated 502s from the BoardLog handler."
  namespace           = local.metrics_namespace
  metric_name         = "ServerErrors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.server_error_alarm_threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = var.alarm_actions
  ok_actions          = var.alarm_actions
}
