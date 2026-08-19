# versions.tf — «паспорт» проєкту.
# Тут ми фіксуємо, ЯКОЮ версією Terraform і ЯКИМИ провайдерами
# цей код дозволено виконувати. Це перше, що читає `terraform init`.

terraform {
  # Мінімальна версія самого Terraform (бінарника).
  # "~> 1.15.0" = «дозволено 1.15.0 ... 1.15.x, але НЕ 1.16».
  # Саме цей рядок читає команда `tfenv min-required`.
  #
  # 🔴 ЧОМУ НЕ "~> 1.15" (без третьої цифри): воно дозволяє і 1.16, і 1.17.
  # Terraform піднімає version у terraform.tfstate до версії того, хто
  # зробив apply, і НАЗАД його вже не відкотити: усі, хто лишився на 1.15,
  # отримають "state snapshot was created by Terraform v1.16.x, which is
  # newer than current v1.15.8" і не зможуть навіть зробити plan.
  # Один студент, що оновив бінарник, паралізує групу.
  # Значення тримаємо синхронним із файлом .terraform-version (1.15.8).
  required_version = "~> 1.15.0"

  required_providers {
    # Провайдер — це плагін, який вміє ходити в API конкретної хмари.
    # Без нього Terraform не знає слова "aws".
    aws = {
      # Звідки качати плагін: registry.terraform.io/hashicorp/aws
      source = "hashicorp/aws"

      # ">= 6.52" — саме стільки вимагає модуль terraform-aws-modules/eks/aws v21.
      # "~> 6.52" додатково забороняє стрибок на 7.x, де можуть бути breaking changes.
      version = "~> 6.52"
    }
  }

  # ─────────────────────────────────────────────────────────────────────────
  # БОНУС (розділ 12 у README): віддалений стейт в S3.
  # Поки закоментовано — стейт лежить локально у файлі terraform.tfstate.
  # ─────────────────────────────────────────────────────────────────────────
  # backend "s3" {
  #   bucket       = "mlops-tfstate-ЗАМІНИ-НА-СВОЄ"
  #   key          = "eks/terraform.tfstate"
  #   region       = "eu-central-1"
  #   encrypt      = true
  #   use_lockfile = true
  # }
}
