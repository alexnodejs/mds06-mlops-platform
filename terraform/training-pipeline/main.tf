# ══════════════════════════════════════════════════════════════════════════
# ТЕМА 10 — АВТОМАТИЗОВАНЕ ТРЕНУВАННЯ
#
# Окремий корінь Terraform, а не додаток до terraform/cluster — навмисно:
#
#   • `terraform apply` тут не чіпає стейт кластера. Помилка в пайплайні не
#     може зачепити 53 ресурси Теми 5.
#   • `make pipeline-down` зносить рівно пайплайн, лишаючи кластер живим.
#   • Студент бачить межу відповідальності: кластер — інфраструктура,
#     пайплайн — застосунок поверх неї.
#
# Звʼязок між коренями — через data-джерела за іменем кластера, а не через
# remote_state. Так Тему 10 можна показати на будь-якому наявному EKS.
# ══════════════════════════════════════════════════════════════════════════

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Кластер шукаємо за іменем. Звідси беруться Endpoint і CertificateAuthority,
# які Step Functions вимагає в кожному виклику eks:runJob.
data "aws_eks_cluster" "this" {
  name = var.cluster_name
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  registry   = "${local.account_id}.dkr.ecr.${var.region}.amazonaws.com"

  trainer_image = var.trainer_image != "" ? var.trainer_image : "${local.registry}/mds06-mlflow-tools:v4"

  # Імʼя state machine потрібне ДО її створення: політика ролі GitHub Actions
  # посилається на цей ARN, а посилання на aws_sfn_state_machine.this.arn
  # звідти дало б цикл залежностей.
  state_machine_name = "mds06-train"
  state_machine_arn  = "arn:aws:states:${var.region}:${local.account_id}:stateMachine:${local.state_machine_name}"
}
