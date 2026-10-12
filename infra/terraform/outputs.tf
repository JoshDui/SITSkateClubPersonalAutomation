output "webhook_function_url" {
  description = "URL to register with Telegram via setWebhook. Pass this to scripts/setwebhook.py."
  value       = aws_lambda_function_url.webhook.function_url
}

output "webhook_lambda_name" {
  description = "For `aws logs tail /aws/lambda/<name> --follow`."
  value       = aws_lambda_function.webhook.function_name
}

output "scheduler_lambda_name" {
  value = aws_lambda_function.scheduler.function_name
}

output "exporter_lambda_name" {
  value = aws_lambda_function.exporter.function_name
}

output "dynamodb_tables" {
  description = "Names of the DynamoDB tables."
  value = {
    members   = aws_dynamodb_table.members.name
    sessions  = aws_dynamodb_table.sessions.name
    responses = aws_dynamodb_table.responses.name
    state     = aws_dynamodb_table.state.name
  }
}

output "ssm_parameter_names" {
  description = "SSM parameters to populate via `aws ssm put-parameter --overwrite` after the first apply."
  value = [
    aws_ssm_parameter.bot_token.name,
    aws_ssm_parameter.webhook_secret.name,
    aws_ssm_parameter.admin_ids.name,
  ]
}
