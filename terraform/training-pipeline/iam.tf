# ══════════════════════════════════════════════════════════════════════════
# ХТО КОМУ ДОВІРЯЄ. Три ролі, три різні призначення — не змішувати:
#
#   github_ci    GitHub Actions -> AWS        через OIDC, без ключів (слайд 32)
#   sfn          Step Functions -> Lambda + EKS
#   lambda       Lambda         -> CloudWatch
# ══════════════════════════════════════════════════════════════════════════

# ── 1. Роль для Step Functions ────────────────────────────────────────────
resource "aws_iam_role" "sfn" {
  name = "mds06-sfn-train"
  tags = var.tags

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
      # Confused deputy: без цієї умови будь-який чужий акаунт, який
      # вгадає ARN ролі, зміг би змусити свою state machine діяти від
      # нашого імені. Умова прибиває роль до нашого акаунта.
      Condition = {
        StringEquals = { "aws:SourceAccount" = local.account_id }
      }
    }]
  })
}

resource "aws_iam_role_policy" "sfn" {
  name = "invoke-lambda-and-log"
  role = aws_iam_role.sfn.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "InvokeOurLambdas"
        Effect = "Allow"
        Action = "lambda:InvokeFunction"
        # Рівно три наші функції, а не "*": роль не повинна вміти викликати
        # чужу Lambda в цьому ж акаунті.
        Resource = values(local.lambda_arns)
      },
      {
        # ⚠️ ЖОДНОГО eks:* ТУТ НЕМАЄ — і це не помилка.
        #
        # eks:runJob НЕ викликає EKS API від імені AWS. Step Functions
        # ходить прямо в Kubernetes API кластера як HTTP-клієнт, а
        # авторизація там своя — RBAC. Тому доступ дається не політикою
        # IAM, а Access Entry (файл access-entry.tf).
        #
        # Якби ми додали сюди eks:DescribeCluster «про всяк випадок», це
        # нічого б не змінило: помилка 401 від Kubernetes від цього не зникає.
        Sid    = "DeliverExecutionLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies", "logs:DescribeLogGroups",
        ]
        # Ці дії не приймають конкретний ресурс — така вимога API доставки
        # логів. Обмежити можна лише самим набором дій.
        Resource = "*"
      },
    ]
  })
}

# ── 2. Роль для Lambda ────────────────────────────────────────────────────
resource "aws_iam_role" "lambda" {
  name = "mds06-lambda-train"
  tags = var.tags

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Логи самої Lambda. Без цієї політики функція працює, але її логів у
# CloudWatch немає — і діагностика перетворюється на вгадування.
resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda_metrics" {
  name = "put-training-metrics"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "cloudwatch:PutMetricData"
      # PutMetricData не підтримує обмеження за ресурсом — лише за
      # namespace через умову. Саме так і звужуємо.
      Resource  = "*"
      Condition = { StringEquals = { "cloudwatch:namespace" = "MDS06/Training" } }
    }]
  })
}
