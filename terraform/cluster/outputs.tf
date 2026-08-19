# outputs.tf — що Terraform надрукує в кінці `apply`.
#
# Output — це спосіб дістати значення з чорної скриньки стейту:
#   - показати людині (як тут),
#   - передати в інший Terraform-проєкт (terraform_remote_state),
#   - зчитати скриптом: `terraform output -raw configure_kubectl`.

output "cluster_name" {
  description = "Імʼя створеного EKS-кластера"
  value       = module.eks.cluster_name
}

output "cluster_endpoint" {
  description = "HTTPS-адреса Kubernetes API Server — саме сюди ходить kubectl (слайд 15)"
  value       = module.eks.cluster_endpoint
}

output "cluster_version" {
  description = "Версія Kubernetes, яку реально підняв AWS"
  value       = module.eks.cluster_version
}

output "node_group_role" {
  description = "IAM-роль worker-нод. Через неї ноди мають право приєднатись до кластера"
  value       = module.eks.node_iam_role_name
}

# Найкорисніший output: готова до копіювання команда.
# Краще, ніж інструкція в README, бо регіон і імʼя підставлені автоматично —
# студент не переплутає їх зі своїми.
output "configure_kubectl" {
  description = "Виконайте цю команду, щоб kubectl побачив кластер"
  value       = "aws eks update-kubeconfig --region ${var.region} --name ${module.eks.cluster_name}"
}
