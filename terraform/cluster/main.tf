# main.tf — тут описано ВСЕ, що ми створюємо в AWS.
#
# Terraform читає всі *.tf файли в теці як один суцільний конфіг.
# Порядок блоків у файлі не важить: Terraform сам будує граф залежностей
# (побачив `module.vpc.vpc_id` всередині module "eks" -> отже VPC треба
# створити раніше).

# ═══════════════════════════════════════════════════════════════════════════
# 1. PROVIDER — «до якої хмари і в який регіон ми підключаємось»
# ═══════════════════════════════════════════════════════════════════════════
provider "aws" {
  # Регіон беремо зі змінної (variables.tf), а не хардкодимо.
  region = var.region

  # Ключі доступу тут НЕ вказуємо — це антипатерн (вони потраплять у git).
  # Провайдер сам знайде їх у ~/.aws/credentials після `aws configure`.

  # Є ще блок `default_tags {}`, який чіпляє теги на КОЖЕН ресурс автоматично.
  # Ми його не використовуємо: нижче теги передаються в модулі явно —
  # так студенту видно, звідки вони беруться.
}

# ═══════════════════════════════════════════════════════════════════════════
# 2. DATA SOURCE — «запитати в AWS те, що ми не створюємо»
# ═══════════════════════════════════════════════════════════════════════════
# `resource` = створити щось. `data` = лише прочитати вже існуюче.
# Тут ми питаємо: які Availability Zones доступні в нашому регіоні?
# Хардкодити ["eu-central-1a", "eu-central-1b"] погано — конфіг перестане
# працювати в іншому регіоні.
data "aws_availability_zones" "available" {
  filter {
    # Відсіюємо Local Zones і Wavelength Zones: у них немає EKS,
    # і вони вимагають окремої активації (opt-in).
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

# ═══════════════════════════════════════════════════════════════════════════
# 3. LOCALS — «локальні змінні», щоб не повторювати те саме
# ═══════════════════════════════════════════════════════════════════════════
# Різниця з variable: local НЕ можна перевизначити ззовні. Це просто
# обчислене значення для внутрішнього використання.
locals {
  # Адресний простір усієї мережі: 10.0.0.0 – 10.0.255.255 (65 536 адрес).
  vpc_cidr = "10.0.0.0/16"

  # Беремо перші 2 зони зі списку, який повернув data source вище.
  # slice(список, від, до) — від 0 включно до 2 НЕ включно, тобто [0, 1].
  # Чому 2? EKS вимагає МІНІМУМ 2 зони. У проді беруть 3.
  azs = slice(data.aws_availability_zones.available.names, 0, 2)

  # Теги для всіх ресурсів — щоб у консолі AWS було видно, що це наше
  # і що воно створене Terraform (а не руками).
  tags = {
    Project   = var.cluster_name
    ManagedBy = "Terraform"
    Lesson    = "MLOps-Topic-5-Kubernetes"
  }
}

# ═══════════════════════════════════════════════════════════════════════════
# 4. VPC — мережа, у якій житиме кластер
# ═══════════════════════════════════════════════════════════════════════════
# Це те, що на слайді 30 названо «VPC — із публічними/приватними сабнетами,
# Internet/NAT Gateway». Ми не пишемо це руками — беремо готовий модуль.
# Модуль = чужий набір .tf файлів, який Terraform завантажить при `init`.
module "vpc" {
  # Звідки взяти модуль: скорочення для registry.terraform.io/terraform-aws-modules/vpc/aws
  source = "terraform-aws-modules/vpc/aws"
  # Пін версії ОБОВʼЯЗКОВИЙ. Без нього завтра приїде нова мажорна версія
  # і зламає ваш конфіг. "~> 6.0" = будь-яка 6.x, але не 7.0.
  version = "~> 6.0"

  # Імʼя VPC у консолі AWS.
  name = "${var.cluster_name}-vpc"
  # Адресний простір з locals вище.
  cidr = local.vpc_cidr

  # У яких зонах створювати підмережі.
  azs = local.azs

  # ── ПРИВАТНІ підмережі: тут будуть worker-ноди й поди ──────────────────
  # Приватні = немає публічної IP-адреси, з інтернету достукатись НЕМОЖЛИВО.
  # cidrsubnet("10.0.0.0/16", 4, k) ріже /16 на шматки /20 (16 + 4 = 20):
  #   k=0 -> 10.0.0.0/20  (4 094 адреси)
  #   k=1 -> 10.0.16.0/20
  # Чому такі великі? Кожен под у EKS отримує РЕАЛЬНУ IP з підмережі (VPC CNI).
  private_subnets = [for k, v in local.azs : cidrsubnet(local.vpc_cidr, 4, k)]

  # ── ПУБЛІЧНІ підмережі: тут будуть NAT Gateway і Load Balancer ─────────
  # cidrsubnet("10.0.0.0/16", 8, k + 48) -> /24 (16 + 8 = 24):
  #   k=0 -> 10.0.48.0/24, k=1 -> 10.0.49.0/24
  # Зсув +48 просто щоб не перетнутись із приватними діапазонами вище.
  public_subnets = [for k, v in local.azs : cidrsubnet(local.vpc_cidr, 8, k + 48)]

  # NAT Gateway дає нодам у приватних підмережах вихід В інтернет
  # (скачати Docker-образ), не даючи інтернету доступ ДО них.
  enable_nat_gateway = true
  # true = ОДИН NAT на всю VPC замість одного на зону.
  # Економія ~$35/міс. Ціна питання: якщо впаде зона з NAT — усі ноди
  # втратять вихід в інтернет. Для навчання ок, для проду — ні.
  single_nat_gateway = true

  # ── Теги, без яких Kubernetes НЕ ЗНАЙДЕ підмережі ──────────────────────
  # Коли ви створите Service type=LoadBalancer, контролер AWS шукатиме
  # підмережі саме за цими тегами. Забули тег -> LoadBalancer вічно
  # висітиме в статусі <pending>.
  public_subnet_tags = {
    "kubernetes.io/role/elb" = 1 # сюди ставити ЗОВНІШНІ балансувальники
  }
  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = 1 # сюди — ВНУТРІШНІ (відповідь на слайд 37)
  }

  tags = local.tags
}

# ═══════════════════════════════════════════════════════════════════════════
# 5. EKS — власне кластер Kubernetes
# ═══════════════════════════════════════════════════════════════════════════
# Офіційний модуль зі слайда 32. Він створює ~40 ресурсів: сам кластер,
# IAM-ролі для control plane і нод, security groups, node group, addons.
module "eks" {
  source = "terraform-aws-modules/eks/aws"
  # УВАГА: у v21 змінні перейменовані порівняно з v20 (cluster_name -> name,
  # cluster_version -> kubernetes_version). Туторіали з інтернету на v20
  # тут НЕ спрацюють — див. розділ «Типові помилки» в docs/05-eks-terraform.md.
  version = "~> 21.0"

  # Імʼя кластера — його ви побачите в консолі і будете вказувати в kubectl.
  name = var.cluster_name
  # Версія Kubernetes для control plane (те, чим керує AWS — слайд 28).
  kubernetes_version = var.kubernetes_version

  # ── Доступ до Kubernetes API ───────────────────────────────────────────
  # true = API server має публічну адресу, і kubectl працює з вашого ноутбука.
  # Для проду ставлять false + доступ через VPN/bastion.
  endpoint_public_access = true
  # Можна звузити до своєї IP замість "весь інтернет" (за замовчуванням 0.0.0.0/0):
  # endpoint_public_access_cidrs = ["ВАША.IP.АДРЕСА/32"]

  # ── Хто має права адміністратора в кластері ────────────────────────────
  # Створює Access Entry для того IAM-користувача, який виконав apply.
  # БЕЗ ЦЬОГО РЯДКА: кластер створиться, але `kubectl get nodes` поверне
  # "error: You must be logged in to the server (Unauthorized)".
  # (Старий спосіб через ConfigMap aws-auth зі слайда 28 — застарілий.)
  enable_cluster_creator_admin_permissions = true

  # ── Addons: системні компоненти кластера ───────────────────────────────
  # Це ті самі компоненти Worker Node зі слайдів 19–22, але керовані AWS:
  addons = {
    coredns    = {} # DNS всередині кластера: імʼя сервісу -> IP
    kube-proxy = {} # мережевий проксі (слайд 22)
    vpc-cni = {
      # Плагін мережі: видає кожному поду реальну IP-адресу з підмережі VPC.
      # before_compute = true -> встановити ЩЕ ДО створення нод.
      # Інакше перші ноди піднімуться зі старою версією CNI і їх доведеться
      # перестворювати.
      before_compute = true
    }

    # ── Агент EKS Pod Identity ──────────────────────────────────────────
    # DaemonSet у kube-system, 1 под на ноду. Сам нічого не робить корисного,
    # але БЕЗ НЬОГО pod_identity_association нижче не працює: association
    # живе в control plane, а креденшели поду віддає саме цей агент.
    # before_compute кладе його в ресурс aws_eks_addon.before_compute, тобто
    # в ранішу фазу графа, ніж драйвер (той чекає на node group).
    eks-pod-identity-agent = {
      before_compute = true
    }

    # ── EBS CSI driver: без нього PersistentVolumeClaim НЕ ПРАЦЮЄ ────────
    # Дефолтний StorageClass gp2 від EKS має provisioner
    # kubernetes.io/aws-ebs — цей in-tree плагін ВИЛУЧЕНО з Kubernetes у 1.31.
    # На 1.34 його не обробляє ніхто, тож будь-який PVC висить у Pending
    # НАЗАВЖДИ і навіть без помилки в Events (нікому нема діла).
    # Живий провізіонер — окремий под, і ось він.
    # Ставить у kube-system Deployment ebs-csi-controller (2 репліки)
    # + DaemonSet ebs-csi-node -> +2 поди на ноду в бюджеті.
    aws-ebs-csi-driver = {
      pod_identity_association = [{
        role_arn = aws_iam_role.ebs_csi.arn
        # Імʼя SA фіксоване самим addon-ом, змінити не можна.
        # Namespace не вказується — для addon-а це завжди kube-system.
        service_account = "ebs-csi-controller-sa"
      }]
      # Дефолт модуля — most_recent = true, тобто КОЖЕН apply перерозвʼязує
      # версію на найновішу (це стосується і трьох addon-ів вище). Якщо
      # потрібно, щоб на парі у всіх було однаково — запініть:
      #   aws eks describe-addon-versions --addon-name aws-ebs-csi-driver \
      #     --kubernetes-version 1.34 --region eu-central-1 \
      #     --query 'addons[0].addonVersions[].addonVersion' --output table
      # addon_version = "v1.5X.X-eksbuild.1"
      # most_recent   = false
    }
  }

  # ── Куди підключити кластер ────────────────────────────────────────────
  # Беремо id мережі з модуля вище. Саме цей рядок і створює залежність
  # «спочатку VPC, потім EKS».
  vpc_id = module.vpc.vpc_id
  # Ноди й поди — тільки в приватних підмережах. Слайд 19: «Pod-и ніколи
  # не живуть на Control Plane — тільки на Worker Nodes».
  subnet_ids = module.vpc.private_subnets

  # ── Worker Nodes (слайд 30: «EKS Node Group або Fargate Profile») ──────
  # Managed Node Group = AWS сам створює Auto Scaling Group, ставить AMI,
  # реєструє ноду в кластері і вміє робити rolling update при апгрейді.
  eks_managed_node_groups = {
    # "default" — довільне імʼя групи. Їх може бути кілька
    # (напр. окрема група з GPU для ML-інференсу).
    default = {
      # Amazon Linux 2023 — дефолтна ОС для EKS починаючи з 1.30.
      ami_type = "AL2023_x86_64_STANDARD"
      # Список типів інстансів. Кілька типів = більше шансів отримати SPOT.
      instance_types = [var.instance_type]
      # ON_DEMAND — стабільно. SPOT дешевший на ~70%, але AWS може забрати
      # інстанс за 2 хвилини попередження. Для пари беремо ON_DEMAND.
      capacity_type = "ON_DEMAND"

      # Межі Auto Scaling Group. desired_size — скільки зараз,
      # min/max — у яких межах може змінюватись (слайд 25).
      #
      # 🔴 ПАСТКА, про яку не пишуть у туторіалах: у модулі на цьому ресурсі
      # стоїть lifecycle { ignore_changes = [scaling_config[0].desired_size] }.
      # Тобто для ВЖЕ СТВОРЕНОЇ групи змінити кількість нод через Terraform
      # НЕМОЖЛИВО — plan просто не побачить різниці. min/max при цьому
      # застосовуються нормально. Додати третю ноду живому кластеру:
      #   aws eks update-nodegroup-config --cluster-name mlops-demo \
      #     --nodegroup-name $(aws eks list-nodegroups --cluster-name mlops-demo \
      #        --region eu-central-1 --query nodegroups[0] --output text) \
      #     --scaling-config minSize=1,maxSize=3,desiredSize=3 --region eu-central-1
      min_size     = var.node_min_size
      max_size     = var.node_max_size
      desired_size = var.node_desired_size

      # ── ⚠️ ЗАМОРОЖЕНА ВЕРСІЯ AMI ────────────────────────────────────────
      # Дефолт модуля use_latest_ami_release_version = true означає, що КОЖЕН
      # plan читає з SSM найновіший AL2023 і показує зміну release_version.
      # AWS випускає AMI приблизно щотижня, тож plan «брудний» постійно, а
      # apply на таку зміну = rolling replacement УСІХ нод (~10 хв, усі поди
      # переїжджають).
      #
      # Для НАВЧАЛЬНОГО кластера ми свідомо пінимо версію:
      #   1) plan лишається чистим і детермінованим — у кожного студента
      #      однаковий результат, а не «залежить від дня тижня»;
      #   2) ноди не перестворюються самі в найгірший момент;
      #   3) підняття третьої ноди дасть той самий AMI, що й у двох наявних.
      #
      # 🔴 У ПРОДІ РОБЛЯТЬ НАВПАКИ: AMI треба регулярно оновлювати, бо в ньому
      # закриваються CVE ядра й containerd. Але роблять це СВІДОМО — у вікно
      # обслуговування, а не випадково разом із іншою правкою. Щоб оновити:
      # підняти значення var.node_ami_release_version і зробити apply,
      # дивлячись на ноди.
      use_latest_ami_release_version = false
      ami_release_version            = var.node_ami_release_version
    }
  }

  tags = local.tags
}
