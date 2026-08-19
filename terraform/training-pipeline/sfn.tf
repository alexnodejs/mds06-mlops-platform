# ══════════════════════════════════════════════════════════════════════════
# STATE MACHINE
#
# Визначення лежить окремим файлом state_machine.asl.json, а не рядком тут.
# Причина практична: у такому вигляді його можна вставити в графічний
# редактор Workflow Studio у консолі AWS, побачити граф і перевірити
# синтаксис — а всередині jsonencode() у HCL це просто нечитабельний рядок.
# ══════════════════════════════════════════════════════════════════════════

resource "aws_sfn_state_machine" "train" {
  name     = local.state_machine_name
  role_arn = aws_iam_role.sfn.arn
  tags     = var.tags

  # STANDARD, а не EXPRESS. EXPRESS дешевший і швидший, але:
  #   • не підтримує .sync-інтеграції (а весь наш пайплайн на eks:runJob.sync)
  #   • DescribeExecution для нього не працює, тобто CI не зміг би дочекатись
  #     результату й повернути ненульовий код
  type = "STANDARD"

  definition = templatefile("${path.module}/state_machine.asl.json", {
    validate_arn    = local.lambda_arns["validate"]
    evaluate_arn    = local.lambda_arns["evaluate"]
    log_metrics_arn = local.lambda_arns["log_metrics"]

    cluster_name = data.aws_eks_cluster.this.name
    endpoint     = data.aws_eks_cluster.this.endpoint
    # Base64 CA кластера. Step Functions підключається до Kubernetes API
    # напряму по HTTPS і перевіряє його сертифікат саме цим ланцюжком.
    certificate_authority = data.aws_eks_cluster.this.certificate_authority[0].data

    namespace     = var.namespace
    trainer_image = local.trainer_image
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
    include_execution_data = true
    level                  = "ERROR"
  }

  # Access Entry мусить існувати ДО першого запуску, інакше EKS.401.
  # Terraform сам цього не виведе: у визначенні машини немає посилання на
  # access entry, тільки на імʼя кластера.
  depends_on = [aws_eks_access_policy_association.sfn]
}

resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/aws/vendedlogs/states/${local.state_machine_name}"
  retention_in_days = 14
  tags              = var.tags
}
