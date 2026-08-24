# Коли зламалось

Перше, куди дивитись. Тут не повний каталог помилок — він у кожної теми свій
(посилання внизу), — а **вісім пасток, які коштували цьому матеріалу найбільше
часу**. Усі вісім траплялись насправді, і жодну не було видно з першого погляду.

```bash
make status     # ноди, Application, поди, PVC, тунелі — почніть звідси
```

---

## 1. `Running` і `Healthy` не означають «працює»

Найдорожча помилка курсу. ArgoCD показує `Synced/Healthy`, `kubectl get pod` —
`Running`, а сервіс порожній.

```
NAME                        READY   STATUS    RESTARTS
monitoring-grafana-xxx      1/3     Running   8
                            ▲
                     ось воно: 1 з 3 контейнерів
```

Два сайдкари вбито по OOM, і саме вони приносять дашборди й datasource.
`STATUS` цього не показує **ніколи** — він про под, а не про контейнери.

> **Правило:** дивіться колонку **READY**. `1/3` при `Running` — це зламаний под.

```bash
kubectl get pods -A | awk '$2 !~ /^([0-9]+)\/\1$/'   # усі, де READY не повний
kubectl -n monitoring logs deploy/monitoring-grafana -c grafana-sc-dashboard
```

---

## 2. Дубльований ключ у YAML зникає мовчки

Два однакові ключі на одному рівні — YAML лишає **останній**, перший зникає без
попередження. Ні `kubectl apply`, ні Helm, ні ArgoCD не скажуть ані слова.

Так одного разу зникло `alertmanager.enabled: false`, а іншого — `kustomize build`
почав падати без зрозумілої причини через два блоки `env:` в одному контейнері.

```bash
python3 -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" файл.yaml   # синтаксис
python3 -c "
import yaml,sys
from collections import Counter
src = open(sys.argv[1]).read()
# найпростіша перевірка: порахувати ключі верхнього рівня очима після safe_load
print(yaml.safe_load(src))
" файл.yaml
```

Правило дешевше за перевірку: **після кожної правки values дивіться, що реально
вийшло**, а не що ви написали.

---

## 3. PVC вічно в `Pending`, і в `Events` тиша

Не «помилка не зрозуміла», а **помилки немає взагалі**. Бо видавати її нікому:
StorageClass `gp2`, який EKS створює сам, використовує провізіонер
`kubernetes.io/aws-ebs` — його **вилучено з Kubernetes у 1.31**, а в нас 1.34.
Контролера, який мав би обробити цей PVC, у кластері фізично немає.

```bash
kubectl get sc                  # рівно ОДИН рядок має містити (default)
kubectl describe pvc <імʼя>     # порожній Events = ось цей випадок
```

Лікується новим класом (`deploy/0-storage/storageclass-gp3.yaml`) — старий не
полагодити, поле `provisioner` незмінне.

---

## 4. `port-forward` не балансує між подами

`kubectl port-forward svc/...` виглядає як звернення до Service, але тунель
прибивається до **одного конкретного пода** і тримається його до розриву.
Оновлення сторінки в браузері завжди дає той самий под.

Це не помилка — це так працює. Але демонстрація «дивіться, запити йдуть на різні
поди» через `port-forward` **не спрацює**. Для неї потрібен запит зсередини
кластера:

```bash
kubectl -n demo-react run curl --rm -it --image=curlimages/curl --restart=Never -- \
  sh -c 'for i in $(seq 6); do curl -s react-app | grep -o "pod-[a-z0-9]*"; done'
```

---

## 5. Мутабельні теги образів

`:latest` (і будь-який тег, який перезаписують) ламає GitOps у два способи: Git
більше не знає, що саме працює в кластері, а ArgoCD не бачить змін — тег же той
самий. Відкочуватись теж нікуди.

У цьому репозиторії теги незмінні: `v1`, `v2`, `v4`. Оновлення застосунку — це
зміна `newTag` у `k8s/*/kustomization.yaml`, коміт і пуш.

---

## 6. Кластер платний увесь час

$0.29 за годину незалежно від того, працює на ньому щось чи ні: control plane,
три ноди і NAT Gateway тарифікуються за факт існування.

```bash
make down           # знести стек, кластер лишається — рахунок іде далі
make cluster-down   # знести все — рахунок у нуль
```

⚠️ **Спершу `make down`, потім `cluster-down`.** LoadBalancer, створені
Kubernetes, Terraform не бачить — вони лишаться в AWS і заблокують видалення
VPC помилкою `DependencyViolation`.

Що ще тихо горить після невдалого прибирання:

```bash
aws ec2 describe-volumes --filters Name=status,Values=available \
  --query 'Volumes[].[VolumeId,Size,CreateTime]' --output table
aws ec2 describe-addresses --query 'Addresses[?AssociationId==null].[PublicIp]' --output table
aws elbv2 describe-load-balancers --query 'LoadBalancers[].LoadBalancerName' --output text
```

---

## 7. Дозволи в AWS і дозволи в Kubernetes — різні системи

`AdministratorAccess` в IAM **не дає** жодних прав усередині кластера, і навпаки.
Це два незалежні механізми, які перетинаються лише в одній точці — Access Entry.

Симптоми, які виглядають однаково, але лікуються по-різному:

| Помилка | Де проблема |
|---|---|
| `kubectl: You must be logged in to the server (Unauthorized)` | не виконано `update-kubeconfig`, або немає Access Entry для вашого користувача |
| `EKS.401` у Step Functions | немає Access Entry для ролі state machine |
| `AccessDenied` від `ebs.csi.aws.com` у подіях PVC | addon стоїть, а IAM-роль для нього — ні |
| `UnauthorizedOperation` у `terraform apply` | бракує прав IAM (це вже справді AWS) |

---

## 8. Еталон і поточні дані з різних джерел = вигаданий дріфт

Найкоротший спосіб отримати правдоподібну брехню на дашборді. Дріфт-монітор
порівнює два розподіли; якщо вони приходять із різних джерел, він чесно покаже
різницю — але це буде різниця **джерел**, а не дріфт даних.

Реальний випадок із Теми 11: тренування й еталон переїхали на
`s3://datasets/iris/v2.csv`, а генератор трафіку лишився на `load_iris()`.
Результат — `drift_detected{feature="petal_length"} = 1` одразу після підйому
стека, без жодної симуляції.

```bash
curl -s localhost:9101/metrics | grep -E "reference_source|drift_p_value"
```

`reference_source{source="builtin"}` замість `storage` означає, що експортер не
дістав датасет і тихо взяв вбудований — усі p-value після цього недостовірні.

Друга половина тієї ж пастки: **джитер генератора треба калібрувати під
еталон**. `JITTER=0.25` було правильним для еталона з 120 рядків і стало
надлишковим для 1200 — KS почав ловити сам джитер.

---

## 9. Тунель мовчки не встає на портах 5000 і 7000

На macOS ці порти тримає **AirPlay Receiver**. `kubectl port-forward` не
скаржиться, а `curl` отримує 403 від AirTunes — і виглядає це як зламаний сервіс
у кластері.

Тому MLflow у цьому репозиторії на **5001**, а дріфт-експортер на **9101**.

---

## Повні каталоги помилок по темах

| Тема | Розділ |
|---|---|
| 5 — Terraform, EKS, сховище | [05-eks-terraform.md § 14](05-eks-terraform.md#14-типові-помилки) |
| 6 — Helm, Kustomize, ArgoCD | [06-deploy-methods.md § 7](06-deploy-methods.md#7-типові-помилки) |
| 8 — Prometheus, Grafana, Loki | [08-monitoring.md](08-monitoring.md#-дефекти-які-знайшло-тільки-живе-розгортання) |
| 9 — MLflow, MinIO, дріфт | [09-mlflow-drift.md](09-mlflow-drift.md#пастки-які-варто-знати-до-того-як-наткнетесь) |
| 10 — Step Functions, Lambda, OIDC | [10-automated-training.md § 10](10-automated-training.md#10-типові-помилки) |
| 11 — реєстр, дані, blue-green | [11-model-registry.md § 12](11-model-registry.md#12-типові-помилки) |

---

## Якщо нічого з переліченого

```bash
kubectl get events -A --sort-by=.lastTimestamp | tail -30
kubectl -n argocd get application -o wide
kubectl describe pod -n <namespace> <под>          # секція Events унизу
kubectl logs -n <namespace> <под> --previous       # логи ПОПЕРЕДНЬОГО запуску
```

`--previous` — те, що найчастіше забувають: под, який щойно перезапустився,
уже не містить логів падіння.
