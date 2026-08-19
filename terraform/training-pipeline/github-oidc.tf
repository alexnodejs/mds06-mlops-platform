# ══════════════════════════════════════════════════════════════════════════
# GITHUB ACTIONS -> AWS БЕЗ ЖОДНОГО КЛЮЧА (слайди 31-32)
#
# На слайді 31 два варіанти: пара AWS_ACCESS_KEY_ID/SECRET у змінних CI —
# і OIDC. Ми беремо другий, і ось у чому різниця на практиці:
#
#   ключі   лежать у налаштуваннях репозиторію вічно. Витік = доступ до
#           акаунта, доки хтось не помітить і не відкличе.
#   OIDC    GitHub видає підписаний JWT на ОДИН запуск job. AWS перевіряє
#           підпис, дивиться, з якого репозиторію й гілки токен, і видає
#           тимчасові креденшели на годину. Красти нічого: у репозиторії
#           не зберігається жоден секрет.
#
# Слайд 32 називає це «одноразовий пропуск з печаткою» — саме так воно й
# працює.
# ══════════════════════════════════════════════════════════════════════════

# ⚠️ data, а НЕ resource. OIDC-провайдер для GitHub — обʼєкт рівня акаунта,
# він може існувати рівно в одному екземплярі. Якщо в акаунті вже є інший
# проєкт із GitHub Actions, провайдер створено ним, і `resource` тут упав би
# з EntityAlreadyExistsException.
#
# Якщо у ВАШОМУ акаунті провайдера ще немає — створіть його один раз:
#   aws iam create-open-id-connect-provider \
#     --url https://token.actions.githubusercontent.com \
#     --client-id-list sts.amazonaws.com
# thumbprint у 2026 році вказувати не треба: IAM бере його сам.
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

locals {
  gh_owner = split("/", var.github_repo)[0]
  gh_repo  = split("/", var.github_repo)[1]
}

data "aws_iam_policy_document" "github_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      # Значення зафіксоване самим провайдером (client_id_list). Інший aud
      # у workflow дасть InvalidIdentityToken.
      values = ["sts.amazonaws.com"]
    }

    condition {
      # StringLike, а не StringEquals — через дві форми claim sub:
      #   стара:      repo:owner/repo:ref:refs/heads/main
      #   незмінна:   repo:owner@1234/repo@5678:ref:refs/heads/main
      # GitHub перейшов на другу в липні 2026, і репозиторії, створені після
      # цього, надсилають саме її. Зірочки закривають лише числові ID —
      # власник, назва репозиторію і гілка лишаються прибитими намертво.
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${var.github_repo}:ref:${var.github_ref}",
        "repo:${local.gh_owner}@*/${local.gh_repo}@*:ref:${var.github_ref}",
      ]
    }
  }
}

resource "aws_iam_role" "github_ci" {
  name               = "mds06-github-actions"
  assume_role_policy = data.aws_iam_policy_document.github_assume.json
  tags               = var.tags

  # Година. Довше не треба: job лише запускає пайплайн і чекає його.
  # Якщо тренування колись триватиме понад годину — креденшели протухнуть
  # посеред очікування, і треба буде або підняти це значення, або не чекати
  # завершення в CI.
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "github_ci" {
  name = "start-and-watch-training"
  role = aws_iam_role.github_ci.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "StartOnlyThisStateMachine"
        Effect = "Allow"
        Action = "states:StartExecution"
        # Рівно одна машина. Роль з GitHub не може запустити нічого іншого
        # в акаунті — навіть якщо хтось перепише workflow.
        Resource = local.state_machine_arn
      },
      {
        Sid    = "WatchOwnExecutions"
        Effect = "Allow"
        Action = ["states:DescribeExecution"]
        # ⚠️ Інший тип ресурсу: StartExecution діє на stateMachine,
        # DescribeExecution — на execution. Одним statement не обійтись.
        Resource = "arn:aws:states:${var.region}:${local.account_id}:execution:${local.state_machine_name}:*"
      },
    ]
  })
}
