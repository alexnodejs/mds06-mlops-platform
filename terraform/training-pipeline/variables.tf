variable "region" {
  description = "Регіон. Мусить збігатися з регіоном кластера."
  type        = string
  default     = "eu-central-1"
}

variable "cluster_name" {
  description = "Імʼя EKS-кластера з Теми 5."
  type        = string
  default     = "mlops-demo"
}

variable "namespace" {
  description = <<-EOT
    Namespace, у якому Step Functions створює Job тренування.
    Доступ ролі обмежений РІВНО цим namespace — див. access-entry.tf.
  EOT
  type        = string
  default     = "mlflow"
}

variable "trainer_image" {
  description = <<-EOT
    Образ із train.py і promote.py. Порожній рядок = зібрати імʼя автоматично
    з вашого акаунта: <account>.dkr.ecr.<region>.amazonaws.com/mds06-mlflow-tools:v5
  EOT
  type        = string
  default     = ""
}

variable "github_repo" {
  description = "Репозиторій, якому дозволено запускати пайплайн: owner/repo"
  type        = string
  default     = "alexnodejs/mds06-mlops-platform"
}

variable "github_ref" {
  description = "Єдина гілка, з якої дозволено запуск."
  type        = string
  default     = "refs/heads/main"
}

variable "min_delta" {
  description = <<-EOT
    Наскільки f1 нової моделі має перевищити чинну, щоб її пустили в прод.
    0 означало б, що будь-яке коливання в четвертому знаку — «покращення»,
    і прод перекочувався б на кожному запуску без користі.
  EOT
  type        = number
  default     = 0.001
}

variable "tags" {
  type = map(string)
  default = {
    Project   = "mlops-demo"
    ManagedBy = "Terraform"
    Lesson    = "MLOps-Topic-10-Automated-Training"
  }
}
