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
