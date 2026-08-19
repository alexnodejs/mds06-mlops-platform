# variables.tf — усі «ручки», які можна покрутити, не чіпаючи main.tf.
#
# Кожна змінна має три частини:
#   description — що це таке (видно в `terraform console` і в docs)
#   type        — тип; Terraform впаде на `plan`, якщо передати не те
#   default     — значення за замовчуванням; якщо його немає, Terraform
#                 інтерактивно спитає значення при кожному запуску
#
# Перевизначити значення можна трьома способами (від найнижчого пріоритету):
#   1) файл terraform.tfvars       ->  cluster_name = "my-cluster"
#   2) змінна оточення             ->  export TF_VAR_cluster_name=my-cluster
#   3) прапорець у команді          ->  terraform apply -var="cluster_name=my-cluster"

variable "region" {
  description = "AWS-регіон, у якому створюємо кластер"
  type        = string
  # eu-central-1 (Франкфурт) — найближчий до України з низькою латентністю.
  # Дешевша альтернатива: eu-north-1 (Стокгольм).
  default = "eu-central-1"
}

variable "cluster_name" {
  description = "Імʼя EKS-кластера. Воно ж піде в теги всіх ресурсів"
  type        = string
  # ВАЖЛИВО: якщо кілька студентів працюють в ОДНОМУ AWS-акаунті —
  # кожен має поставити своє унікальне імʼя, інакше буде конфлікт.
  default = "mlops-demo"
}

variable "kubernetes_version" {
  description = "Версія Kubernetes для control plane"
  type        = string
  # 1.34 — стабільна версія у standard support.
  # Тримайте різницю з вашим kubectl не більше ніж ±1 мінорна версія.
  default = "1.34"
}

variable "instance_type" {
  description = "Тип EC2-інстансів для worker-нод"
  type        = string
  # t3.medium: 2 vCPU / 4 GiB / до ~17 подів на ноду.
  # t3.small був би дешевшим, але вміщає лише ~11 подів — на демо з HPA замало.
  default = "t3.medium"
}

variable "node_desired_size" {
  description = "Скільки нод тримати зараз"
  type        = number
  # 🔴 НЕ СТАВТЕ 1 і НЕ СТАВТЕ 2. Бінд тут — не CPU і не памʼять, а ЛІМІТ
  # ПОДІВ НА НОДУ: t3.medium вміщає ~17 подів, тобто 2 ноди = 34 слоти.
  # Арифметика повного стека курсу: 13 системних (argocd 7 + kube-system 6)
  # + 11 (моніторинг Теми 8) + 4 (ebs-csi: controller 2 + node DaemonSet 2)
  # + 2 (eks-pod-identity-agent) + 3 (MinIO/Postgres/MLflow) + 1 експортер
  # = РІВНО 34 з 34. Нуль вільних слотів, і провал приходить не там, де його
  # чекають: транзієнтний Job (створення bucket у MinIO, тренування моделі)
  # не отримує слота -> Job у Pending -> ArgoCD назавжди Progressing.
  # Третя нода = +17 слотів за $0.0456/год (~$0.36 за заняття) — дешевше за
  # будь-яку хвилину, витрачену на «чому ArgoCD зламався».
  # На Теми 5-6 (лише nginx) достатньо 2: terraform apply -var node_desired_size=2
  #
  # УВАГА: це значення діє лише при СТВОРЕННІ node group. Модуль ігнорує
  # подальші зміни desired_size (ignore_changes — див. комент у main.tf),
  # тож живому кластеру ноду додають через aws eks update-nodegroup-config.
  default = 3
}

variable "node_ami_release_version" {
  description = "Заморожена версія AMI Amazon Linux 2023 для worker-нод"
  type        = string
  # Формат: <версія k8s>-<дата збірки>. Мусить збігатися з kubernetes_version
  # (1.34), інакше AWS відкине node group: "Requested release version
  # 1.33.x is not valid for kubernetes version 1.34".
  #
  # Пін потрібен, щоб група отримувала ОДИН І ТОЙ САМИЙ AMI протягом курсу:
  # AWS випускає нову збірку приблизно щотижня, і без піна у студента, який
  # робив apply у понеділок, і в того, хто в пʼятницю, будуть різні ядро й
  # containerd — а «у мене працює, у тебе ні» на занятті не діагностується.
  # Детальніше про наслідки — коментар біля eks_managed_node_groups у main.tf.
  default = "1.34.9-20260801"
}

variable "node_min_size" {
  description = "Нижня межа: менше цього autoscaler не опустить"
  type        = number
  default     = 1
}

variable "node_max_size" {
  description = "Верхня межа: більше цього кластер не виросте (захист від рахунку на $1000)"
  type        = number
  # Мусить бути >= node_desired_size, інакше AWS відкине node group.
  # І НІКОЛИ не ставте max = 1 «щоб зекономити»: тоді кластер, що зсівся
  # до однієї ноди, не зможе вирости назад навіть уручну.
  default = 3

  # Ловимо max < desired ще на `plan`, за секунди. Без цієї перевірки
  # Terraform спокійно піде в apply, і про помилку скаже AWS — через ~10 хв
  # очікування node group: "InvalidParameterException: Desired capacity N
  # can't be greater than max size M". Кластер при цьому вже створено,
  # і студент сидить із половиною ресурсів.
  validation {
    condition     = var.node_max_size >= var.node_desired_size
    error_message = "node_max_size (${var.node_max_size}) мусить бути >= node_desired_size (${var.node_desired_size})."
  }
}
