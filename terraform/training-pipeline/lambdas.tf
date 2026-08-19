# ══════════════════════════════════════════════════════════════════════════
# ТРИ LAMBDA-ФУНКЦІЇ (слайд 26: lambda/validate.py, lambda/log_metrics.py)
#
# ZIP збирається провайдером archive прямо під час apply — жодних .zip у Git
# і жодного окремого кроку збірки. Змінили handler.py -> змінився хеш ->
# terraform сам перезаллє функцію.
#
# Залежностей у функцій немає: boto3 уже є в рантаймі Lambda, решта — stdlib.
# Саме тому тут не потрібні ні шари, ні контейнерні образи.
# ══════════════════════════════════════════════════════════════════════════

locals {
  lambdas = {
    validate = {
      description = "Перевіряє параметри з CI до того, як витрачено ресурси"
      timeout     = 10
      env         = {}
    }
    evaluate = {
      description = "Quality gate: нова модель краща за чинну чи ні"
      timeout     = 30
      env         = { MIN_DELTA = tostring(var.min_delta) }
    }
    log_metrics = {
      description = "Пише підсумок прогону в CloudWatch"
      timeout     = 30
      env         = { METRIC_NAMESPACE = "MDS06/Training" }
    }
  }
}

data "archive_file" "lambda" {
  for_each = local.lambdas

  type        = "zip"
  source_file = "${path.module}/../../lambdas/${each.key}/handler.py"
  output_path = "${path.module}/.build/${each.key}.zip"
}

resource "aws_lambda_function" "this" {
  for_each = local.lambdas

  function_name = "mds06-${replace(each.key, "_", "-")}"
  description   = each.value.description
  role          = aws_iam_role.lambda.arn
  handler       = "handler.handler"
  runtime       = "python3.13"
  timeout       = each.value.timeout
  memory_size   = 256
  tags          = var.tags

  filename = data.archive_file.lambda[each.key].output_path
  # Без source_code_hash Terraform не бачить зміни в коді: ім'я файла те саме,
  # і apply після правки handler.py нічого б не зробив.
  source_code_hash = data.archive_file.lambda[each.key].output_base64sha256

  environment {
    variables = merge(each.value.env, { PYTHONUNBUFFERED = "1" })
  }
}

# Явні лог-групи з терміном зберігання. Без них Lambda створює групу сама —
# з retention = «назавжди», і логи навчального проєкту капають у рахунок роками.
resource "aws_cloudwatch_log_group" "lambda" {
  for_each = local.lambdas

  name              = "/aws/lambda/${aws_lambda_function.this[each.key].function_name}"
  retention_in_days = 14
  tags              = var.tags
}

# Псевдоніми для читабельності в решті коду.
locals {
  lambda_arns = { for k, v in aws_lambda_function.this : k => v.arn }
}
