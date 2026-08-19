# ebs-csi-iam.tf — IAM-роль, з якою под ebs-csi-controller ходить в EC2 API
# створювати й підключати диски.
#
# Драйвер сам по собі безсилий: створити EBS-том — це виклик ec2:CreateVolume,
# а под за замовчуванням не має жодних AWS-прав. Тобто без цього файлу addon
# встановиться, покаже ACTIVE, і кожен PVC впаде з AccessDenied — це гірше за
# Pending, бо виглядає як «драйвер зламаний».
#
# Чому роль тут, а не «засобами модуля»: у terraform-aws-modules/eks/aws v21
# сабмодуля для IRSA/pod-identity ролей НЕМАЄ (його прибрали в 20.0). Є окремий
# модуль terraform-aws-modules/eks-pod-identity/aws, але для ОДНОЇ ролі три
# звичайні ресурси коротші за ще одну зовнішню залежність.

# Trust policy: «хто взагалі має право взяти цю роль».
data "aws_iam_policy_document" "ebs_csi_assume" {
  statement {
    sid    = "AllowEksAuthToAssumeRoleForPodIdentity"
    effect = "Allow"
    # sts:TagSession обовʼязковий: EKS Auth API вішає на сесію теги
    # (імʼя кластера, namespace, SA) і без цього дозволу отримує AccessDenied.
    actions = ["sts:AssumeRole", "sts:TagSession"]

    principals {
      type = "Service"
      # НЕ ec2.amazonaws.com і НЕ eks.amazonaws.com. Саме pods.* — це сервіс
      # EKS Pod Identity. Помилка в цьому рядку дає «не бачить креденшелів».
      identifiers = ["pods.eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ebs_csi" {
  name        = "${var.cluster_name}-ebs-csi-driver"
  description = "IAM role for aws-ebs-csi-driver EKS addon via EKS Pod Identity"

  assume_role_policy = data.aws_iam_policy_document.ebs_csi_assume.json

  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "ebs_csi" {
  role = aws_iam_role.ebs_csi.name

  # Керована AWS політика — свою писати не треба.
  #
  # ⚠️ ШЛЯХ У ARN НЕ ІНТУЇТИВНИЙ, і тут легко втратити півгодини.
  # Перевірено через `aws iam get-policy` 17.08.2026:
  #   arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy    ✅ існує (V1)
  #   arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicyV2  ❌ НЕ ІСНУЄ
  #   arn:aws:iam::aws:policy/AmazonEBSCSIDriverPolicyV2               ✅ існує (V2)
  # Тобто V1 лежить під service-role/, а V2 — у корені. Помилка дасть
  # "NoSuchEntity" уже на apply, а не на plan: plan не звіряє існування
  # керованих політик.
  #
  # Беремо V2: у неї вужчий скоуп прав (менше можна зламати).
  policy_arn = "arn:aws:iam::aws:policy/AmazonEBSCSIDriverPolicyV2"
}

# Права ноди доробляти НЕ треба: агент вимагає eks-auth:AssumeRoleForPodIdentity,
# і воно входить в AmazonEKSWorkerNodePolicy, яку модуль чіпляє за замовчуванням.
# Перевірити association після apply:
#   aws eks list-pod-identity-associations --cluster-name mlops-demo --region eu-central-1
output "ebs_csi_role_arn" {
  description = "ARN ролі, яку EKS віддає поду ebs-csi-controller"
  value       = aws_iam_role.ebs_csi.arn
}

# ═══════════════════════════════════════════════════════════════════════════
# АЛЬТЕРНАТИВА: IRSA замість Pod Identity — коли бракує СЛОТІВ ПОДІВ.
# Економить 2 поди (addon eks-pod-identity-agent стає непотрібним), а
# OIDC-провайдер у кластері вже є: enable_irsa = true — дефолт модуля.
# Ціна: AWS називає IRSA «previous method», і trust policy довша.
#
# Перехід:
#   1) прибрати з addons блок eks-pod-identity-agent;
#   2) в aws-ebs-csi-driver замінити pod_identity_association на
#      service_account_role_arn = aws_iam_role.ebs_csi.arn
#   3) замінити data-блок вище на:
#
# data "aws_iam_policy_document" "ebs_csi_assume" {
#   statement {
#     effect  = "Allow"
#     actions = ["sts:AssumeRoleWithWebIdentity"]
#     principals {
#       type        = "Federated"
#       identifiers = [module.eks.oidc_provider_arn]
#     }
#     condition {
#       test     = "StringEquals"
#       variable = "${module.eks.oidc_provider}:aud"
#       values   = ["sts.amazonaws.com"]
#     }
#     # Без умови :sub роль зможе взяти ЛЮБИЙ под кластера — класична дірка.
#     condition {
#       test     = "StringEquals"
#       variable = "${module.eks.oidc_provider}:sub"
#       values   = ["system:serviceaccount:kube-system:ebs-csi-controller-sa"]
#     }
#   }
# }
# ═══════════════════════════════════════════════════════════════════════════
