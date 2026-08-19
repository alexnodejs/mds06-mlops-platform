# ══════════════════════════════════════════════════════════════════════════
# ДОСТУП STEP FUNCTIONS ВСЕРЕДИНУ КЛАСТЕРА
#
# Найважливіший файл Теми 10 і найчастіше джерело помилки EKS.401.
#
# Дозволи в AWS і дозволи в Kubernetes — ДВІ РІЗНІ СИСТЕМИ. IAM вирішує,
# чи можна викликати AWS API. Але eks:runJob не викликає AWS API: Step
# Functions відкриває HTTPS-зʼєднання просто до Kubernetes API кластера й
# авторизується там як IAM-принципал. Хто цей принципал усередині кластера —
# вирішує вже Kubernetes.
#
# Раніше цей звʼязок робили через ConfigMap aws-auth (слайд 28). Тепер —
# через Access Entry: це ресурс AWS, тобто ним керує Terraform, а не
# редагування ConfigMap руками з ризиком вибити з кластера самого себе.
#
# ЯК ПЕРЕВІРИТИ, ЩО ДОЇХАЛО:
#   aws eks list-associated-access-policies --cluster-name mlops-demo \
#     --principal-arn <ARN ролі з виводу terraform>
# ══════════════════════════════════════════════════════════════════════════

resource "aws_eks_access_entry" "sfn" {
  cluster_name  = var.cluster_name
  principal_arn = aws_iam_role.sfn.arn
  type          = "STANDARD"
  tags          = var.tags
}

resource "aws_eks_access_policy_association" "sfn" {
  cluster_name  = var.cluster_name
  principal_arn = aws_iam_role.sfn.arn

  # EditPolicy дає рівно те, що потрібно runJob.sync:
  #   batch/jobs           create, get, list, watch, delete
  #   pods                 get, list, watch     (SFN шукає под свого Job)
  #   pods/log             get                  (LogOptions.RetrieveLogs)
  policy_arn = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSEditPolicy"

  access_scope {
    # ⭐ namespace, а не cluster. Роль може створювати Job РІВНО в mlflow і
    # ніде більше. Із type = "cluster" та сама політика дала б їй право
    # правити будь-що в kube-system.
    type       = "namespace"
    namespaces = [var.namespace]
  }

  depends_on = [aws_eks_access_entry.sfn]
}
