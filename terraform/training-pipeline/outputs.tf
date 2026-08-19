output "state_machine_arn" {
  description = "Підставте це значення у STATE_MACHINE_ARN у .github/workflows/train.yml"
  value       = aws_sfn_state_machine.train.arn
}

output "github_role_arn" {
  description = "Підставте це у role-to-assume у .github/workflows/train.yml"
  value       = aws_iam_role.github_ci.arn
}

output "console_url" {
  description = "Граф виконань у консолі AWS — саме його показують студентам"
  value       = "https://${var.region}.console.aws.amazon.com/states/home?region=${var.region}#/statemachines/view/${aws_sfn_state_machine.train.arn}"
}

output "run_command" {
  description = "Запуск пайплайну з термінала, без GitHub"
  value       = "make pipeline-run"
}

output "sfn_role_arn" {
  description = "Роль state machine. Саме її треба шукати у списку Access Entry кластера."
  value       = aws_iam_role.sfn.arn
}
