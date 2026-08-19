# Чотири способи деплою в Kubernetes

**Тема 6. Деплой ML-сервісів у Kubernetes (EKS) — практична частина**
Курс MLOps CI/CD 2.0

Продовження [README.md](README.md) (Тема 5). Кластер уже піднято — тепер
розбираємось, **як саме** в нього доставляти застосунки.

Ми задеплоїмо **один і той самий nginx чотирма різними способами**. Кожен
покаже **свою кольорову сторінку**, тож наприкінці ви відкриєте шість вкладок
браузера і побачите наочно, хто що зробив.

> Усе перевірено офлайн: `helm lint` ✅, `helm template` ✅,
> `kubectl kustomize` ✅ для всіх трьох overlays.

---

## Зміст

| # | Спосіб | Що показує | Час |
|---|--------|-----------|-----|
| 0 | [Що будемо робити](#0-що-будемо-робити) | — | 5 хв |
| 1 | [`kubectl apply`](#1-kubectl-apply--ручний-деплой) | базу, «як воно влаштовано» | 10 хв |
| 2 | [Helm](#2-helm--шаблонізований-деплой) | шаблони + `values.yaml` | 20 хв |
| 3 | [Kustomize](#3-kustomize--накладання-overlay) | dev/stage/prod без копіпасту | 20 хв |
| 4 | [**ArgoCD**](#4-argocd--gitops) | GitOps, self-heal, аудит | 35 хв |
| 5 | [Усі чотири поруч](#5-усі-чотири-поруч) | фінальне порівняння | 10 хв |
| 6 | [Прибирання](#6-прибирання) | | 5 хв |
| 7 | [Типові помилки](#7-типові-помилки) | довідка | — |

---

## 0. Що будемо робити

### Чотири підходи (слайд 8)

| Підхід | Сценарій | Переваги | Основне обмеження |
|---|---|---|---|
| **kubectl** | простий запуск / дебаг | прозоро, гнучко | немає параметризації |
| **Helm** | деплой готових застосунків | шаблони, простота | треба розуміти шаблони |
| **Kustomize** | різні середовища | патчинг, DRY | менша гнучкість за Helm |
| **ArgoCD** | автоматичний GitOps | CI/CD, self-heal, audit | вищий поріг впровадження |

**Усі чотири зрештою створюють ті самі YAML-ресурси Kubernetes.** Різниця —
у рівні автоматизації і в тому, *хто* виконує `apply`.

### Що вийде

| # | Спосіб | Namespace | Колір сторінки | Порт |
|---|---|---|---|---|
| 1 | kubectl | `demo-kubectl` | 🔵 синій | 8081 |
| 2 | Helm | `demo-helm` | 🔷 темно-синій | 8082 |
| 3 | Kustomize · dev | `demo-dev` | 🟢 зелений | 8083 |
| 3 | Kustomize · prod | `demo-prod` | 🔴 червоний | 8084 |
| 4 | ArgoCD | `demo-gitops` | 🟠 помаранчевий | 8085 |
| 4 | ArgoCD + Helm | `demo-gitops-helm` | 🟣 фіолетовий | 8086 |

### Структура файлів

```
deploy/
├── 1-kubectl/                    ← спосіб 1: чотири окремі YAML
│   ├── 00-namespace.yaml         префікси задають ПОРЯДОК застосування:
│   ├── 01-configmap.yaml         HTML лежить УСЕРЕДИНІ yaml
│   ├── 02-deployment.yaml        `kubectl apply -f <тека>/` іде за алфавітом,
│   └── 03-service.yaml           і без 00-/01- namespace застосувався б третім
├── 2-helm/nginx-demo/            ← спосіб 2: чарт
│   ├── Chart.yaml                мета-інформація
│   ├── values.yaml               значення за замовчуванням
│   ├── values-prod.yaml          що відрізняється у prod
│   └── templates/                шаблони з {{ }}
│       ├── _helpers.tpl
│       ├── configmap.yaml
│       ├── deployment.yaml
│       ├── service.yaml
│       └── NOTES.txt
├── 3-kustomize/                  ← спосіб 3: база + накладки
│   ├── base/                     спільне для всіх середовищ
│   │   ├── kustomization.yaml
│   │   ├── deployment.yaml
│   │   ├── service.yaml
│   │   └── index.html            звичайний HTML-файл, не всередині YAML
│   └── overlays/
│       ├── dev/                  2 репліки, зелений
│       ├── prod/                 3 репліки, більше ресурсів, червоний
│       └── gitops/               для ArgoCD, помаранчевий
└── 4-argocd/                     ← спосіб 4: GitOps
    ├── application-kustomize.yaml
    └── application-helm.yaml
```

### Скільки коштує

**Нічого понад те, що вже платите за кластер із Теми 5.** Усі сервіси тут —
`ClusterIP`, дивимось через `kubectl port-forward`. Жодного Load Balancer:
шість штук коштували б ~$0.18/год і потім блокували б `terraform destroy`.

> ### ⚠️ Ліміт подів
> На `2 × t3.medium` максимум ~34 поди. Порахуємо: система 6 + ArgoCD 7 +
> усі шість демо 13 = **26**. Влізає, але без великого запасу.
> Якщо побачите поди в `Pending` — підніміть `node_desired_size` до 3
> у `terraform/variables.tf` і зробіть `terraform apply`.

---

## 1. `kubectl apply` — ручний деплой

> Слайд 10. Базовий спосіб: ви пишете YAML і застосовуєте його руками.

### Що тут нового порівняно з Темою 5

Додався **ConfigMap** — сховище неcекретної конфігурації. Ми кладемо в нього
цілу HTML-сторінку і монтуємо в контейнер як файл:

```yaml
# 01-configmap.yaml — ключ стане ІМЕНЕМ ФАЙЛУ
data:
  index.html: |
    <!doctype html>
    ...
```

```yaml
# 02-deployment.yaml — і монтуємо його в nginx
volumeMounts:
  - name: html
    mountPath: /usr/share/nginx/html   # index.html стане головною сторінкою
volumes:
  - name: html
    configMap:
      name: nginx-html
```

Так само у реальному ML-сервісі в ConfigMap кладуть `config.yaml`, пороги
моделі, назви фіч. Для паролів і токенів є окремий тип — **Secret**.

### Деплой

```bash
cd ~/Repos/goit/mds06-mlops-platform

# -f з текою застосує ВСІ yaml усередині — за АЛФАВІТОМ імен файлів.
# Саме тому файли названі 00-namespace, 01-configmap, ...: без префіксів
# першим пішов би configmap.yaml і apply впав би з
#   Error from server (NotFound): namespaces "demo-kubectl" not found
kubectl apply -f deploy/1-kubectl/
```

```
namespace/demo-kubectl created
configmap/nginx-html created
deployment.apps/nginx-demo created
service/nginx-demo created
```

### Дивимось

```bash
kubectl get all -n demo-kubectl

# port-forward тримає тунель, поки працює. Ctrl+C — закрити.
kubectl port-forward -n demo-kubectl svc/nginx-demo 8081:80
```

Відкрийте **http://localhost:8081** — синя сторінка «1 · kubectl apply».

### Де болить

Спробуйте уявити, що вам потрібен другий такий стенд — для stage.
Доведеться **скопіювати всі чотири файли** і в кожному замінити namespace,
імена, кількість реплік. Через три середовища ви маєте 12 файлів, у яких
90% тексту однакові, і будь-яка правка робиться тричі.

Саме цю проблему вирішують наступні два способи.

---

## 2. Helm — шаблонізований деплой

> Слайди 11, 19–22, 26–30. Helm — це пакетний менеджер для Kubernetes,
> «як apt або yum, тільки для кластерів».

### Перевірка, що Helm є

```bash
helm version --short
```

Якщо немає: `brew install helm`.

### Структура чарту (слайд 21)

```
nginx-demo/
├── Chart.yaml        мета-інформація: імʼя, версія
├── values.yaml       ЗНАЧЕННЯ
└── templates/        ШАБЛОНИ, які ці значення споживають
    ├── _helpers.tpl  файли з "_" не стають ресурсами
    ├── configmap.yaml
    ├── deployment.yaml
    ├── service.yaml
    └── NOTES.txt     що надрукувати після install
```

**Все, що всередині `templates/`, рендериться у звичайні
Kubernetes-ресурси.** Helm нічого не додає до Kubernetes — він лише
допомагає ці YAML породжувати.

### Дві версії в Chart.yaml, які плутають (слайд 27)

```yaml
version: 0.1.0      # версія САМОГО ЧАРТУ — піднімайте при зміні шаблонів
appVersion: "1.29"  # версія ДОДАТКА всередині — тут версія nginx
```

### Як значення потрапляють у шаблон (слайд 28)

`values.yaml`:
```yaml
replicaCount: 2
page:
  accent: "#0F1689"
  title: "Helm"
```

`templates/deployment.yaml`:
```yaml
spec:
  replicas: {{ .Values.replicaCount }}
```

### Спочатку подивитись, потім застосувати

Головна звичка при роботі з Helm — **дивитись, що згенерувалось**, до того
як це потрапить у кластер:

```bash
cd deploy/2-helm

helm lint ./nginx-demo        # синтаксис і структура чарту
helm template demo ./nginx-demo   # надрукувати готовий YAML, нічого не застосовуючи
```

`helm template` — ваш найкращий друг при відладці. Він показує рівно те, що
Helm відправить у кластер.

### Деплой (слайд 30)

```bash
helm install nginx-demo ./nginx-demo \
  --namespace demo-helm \
  --create-namespace
```

- `nginx-demo` — **імʼя релізу**. Один чарт можна поставити багато разів під
  різними іменами, вони не зіткнуться.
- `--create-namespace` — створити namespace, якщо його немає.

Після встановлення Helm надрукує `NOTES.txt` з готовою командою port-forward.

```bash
kubectl port-forward -n demo-helm svc/nginx-demo 8082:80
```

**http://localhost:8082** — темно-синя сторінка «2 · Helm».

### ⭐ Головна демонстрація: оновлення

Ось заради чого все це:

```bash
# Змінити колір сторінки однією командою, не чіпаючи жодного YAML
helm upgrade nginx-demo ./nginx-demo -n demo-helm \
  --set page.accent="#00897B" \
  --set page.title="Helm після upgrade"

# Оновіть вкладку 8082 — сторінка позеленіла
```

> **Чому це взагалі спрацювало.** Kubernetes **не перезапускає поди**, коли
> змінився лише ConfigMap. Тобто без додаткових зусиль ви б побачили
> «upgraded» у терміналі й **стару сторінку** в браузері.
>
> У `templates/deployment.yaml` є рядок, який це вирішує:
> ```yaml
> checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
> ```
> Хеш вмісту ConfigMap потрапляє в анотацію пода. Змінився HTML → змінився
> хеш → змінився шаблон пода → Deployment робить rolling update.
> Це стандартний прийом Helm, і його доводиться писати руками.
> **Запамʼятайте — у Kustomize це працює з коробки** (спосіб 3).

### Історія і відкат

```bash
helm history nginx-demo -n demo-helm      # усі ревізії
helm rollback nginx-demo 1 -n demo-helm   # повернутись до першої
helm get values nginx-demo -n demo-helm   # з якими значеннями стоїть зараз
```

Оновіть вкладку — колір повернувся. Helm зберігає всі ревізії релізу
в кластері.

### Те саме, але prod

```bash
helm install nginx-prod ./nginx-demo \
  -n demo-helm-prod --create-namespace \
  -f nginx-demo/values.yaml \
  -f nginx-demo/values-prod.yaml
```

Файли значень **зливаються** зліва направо: `values-prod.yaml` містить лише
те, що відрізняється (3 репліки, червоний колір, більше ресурсів). Шаблони
не змінювались взагалі.

> Це необовʼязковий крок — він додасть ще 3 поди. Якщо кластер маленький,
> просто подивіться різницю через `helm template`:
> ```bash
> diff <(helm template x ./nginx-demo) \
>      <(helm template x ./nginx-demo -f nginx-demo/values-prod.yaml)
> ```

---

## 3. Kustomize — накладання overlay

> Слайд 13. Kustomize описує базову конфігурацію і створює варіації для
> різних середовищ **без дублювання YAML**.

### Встановлювати нічого не треба

Kustomize **вбудований у kubectl**:

```bash
kubectl version --client | grep -i kustomize
# Kustomize Version: v5.7.1
```

Прапорець `-k` замість `-f` — і все:

```bash
kubectl apply -k <тека>
```

### Ключова відмінність від Helm

| | Helm | Kustomize |
|---|---|---|
| Підхід | **шаблонізатор**: `{{ .Values.x }}` | **накладання патчів** на готовий YAML |
| Базові файли | не є валідним Kubernetes YAML | **є** валідним YAML, працюють самі по собі |
| Гнучкість | дуже висока (цикли, умови, функції) | обмежена тим, що можна пропатчити |
| Складність | треба вивчити мову шаблонів | треба знати структуру своїх ресурсів |

У `base/deployment.yaml` **немає жодної фігурної дужки** — це звичайний
робочий маніфест.

### Структура

```
3-kustomize/
├── base/                  спільне
│   ├── kustomization.yaml
│   ├── deployment.yaml    replicas: 1, без namespace
│   ├── service.yaml
│   └── index.html         ← ЗВИЧАЙНИЙ файл, не всередині YAML
└── overlays/
    ├── dev/    → namespace demo-dev,  префікс dev-,  2 репліки, зелений
    ├── prod/   → namespace demo-prod, префікс prod-, 3 репліки, червоний, патч ресурсів
    └── gitops/ → для ArgoCD (спосіб 4)
```

### Що робить overlay

```yaml
# overlays/dev/kustomization.yaml
resources:
  - namespace.yaml
  - ../../base        # ← база НЕ копіюється, лише посилання

namespace: demo-dev   # проставити namespace усім ресурсам
namePrefix: dev-      # nginx-demo -> dev-nginx-demo
labels:
  - pairs: { env: dev }
replicas:
  - name: nginx-demo
    count: 2
configMapGenerator:
  - name: nginx-html
    behavior: replace  # замінити HTML з бази своїм
    files: [index.html]
```

**У overlay лежить тільки різниця.** Порівняйте `dev` і `prod` — вони
відрізняються чотирма значеннями.

### Спочатку подивитись

Як і в Helm, є команда «покажи, що вийде, нічого не застосовуючи»:

```bash
cd deploy/3-kustomize

kubectl kustomize overlays/dev     # надрукувати результат
```

Порівняти два середовища одним рядком:

```bash
diff <(kubectl kustomize overlays/dev) <(kubectl kustomize overlays/prod)
```

### ⭐ Фокус із configMapGenerator

Знайдіть у виводі імʼя ConfigMap:

```
name: dev-nginx-html-mgght5tctm
```

Звідки хвіст? Kustomize додав **хеш вмісту файлу**. І — найголовніше —
він переписав посилання на цей ConfigMap **і в Deployment теж**:

```yaml
volumes:
  - configMap:
      name: dev-nginx-html-mgght5tctm   # ← те саме імʼя з хешем
```

Наслідок: **змінили `index.html` → змінився хеш → змінилось імʼя ConfigMap
→ змінився под → rolling update стався сам.**

Це рівно те, заради чого в Helm довелось писати `checksum/config` руками.
Kustomize робить це з коробки.

### Деплой

```bash
kubectl apply -k overlays/dev
kubectl apply -k overlays/prod

kubectl get pods -n demo-dev
kubectl get pods -n demo-prod
```

```bash
kubectl port-forward -n demo-dev  svc/dev-nginx-demo  8083:80
kubectl port-forward -n demo-prod svc/prod-nginx-demo 8084:80
```

**http://localhost:8083** — 🟢 зелений dev
**http://localhost:8084** — 🔴 червоний prod

### Демонстрація автоматичного оновлення

```bash
# Змінити текст у dev
sed -i '' 's/Kustomize · dev/Kustomize · ЗМІНЕНО/' overlays/dev/index.html

kubectl apply -k overlays/dev
kubectl get pods -n demo-dev -w    # видно rolling update
```

Оновіть 8083 — текст змінився. Жодних анотацій писати не довелось.

```bash
# повернути як було
sed -i '' 's/Kustomize · ЗМІНЕНО/Kustomize · dev/' overlays/dev/index.html
```

> На Linux замість `sed -i ''` пишіть `sed -i`.

---

## 4. ArgoCD — GitOps

> Слайд 17. **GitOps — це підхід, при якому стан кластера визначається
> Git-репозиторієм.** Замість ручного `kubectl apply` ви пушите зміни в Git,
> а ArgoCD синхронізує кластер із Git автоматично.

Це найважливіший спосіб із чотирьох, і саме він — тема наступного заняття
(слайд 39).

### Що змінюється принципово

| | Способи 1–3 | GitOps |
|---|---|---|
| Хто виконує apply | **людина** з ноутбука | **контролер усередині кластера** |
| Джерело істини | те, що востаннє застосували | **Git-репозиторій** |
| Хто має доступ до кластера | кожен інженер | тільки ArgoCD |
| Ручна зміна в кластері | залишиться назавжди | **буде відкочена** (self-heal) |
| Історія змін | немає | `git log` |

### 4.1. Репозиторій має бути в Git

ArgoCD читає **тільки з Git**. Це не опція — це весь сенс підходу.

```bash
cd ~/Repos/goit/mds06-mlops-platform

git init
git add .
git commit -m "Теми 5-6: Terraform EKS + чотири способи деплою"
```

Далі публікуємо. Через GitHub CLI:

```bash
gh repo create mds06-mlops-platform --public --source=. --push
```

Або руками: створіть **публічний** репозиторій на github.com і

```bash
git remote add origin https://github.com/ВАШ-ЛОГІН/mds06-mlops-platform.git
git push -u origin main
```

> **Чому публічний?** Щоб не морочитись із креденшелами на парі. Для
> приватного треба `argocd repo add <url> --username ... --password <token>`.
>
> **Для заняття** найшвидше: викладач пушить репозиторій один раз, усі
> студенти вказують на нього. Демо self-heal працюватиме в кожного.
> А от «змінив → запушив → само оновилось» кожен зробить у своєму форку.

### 4.2. Встановлюємо ArgoCD

```bash
kubectl create namespace argocd

# ⚠️ ОБОВʼЯЗКОВО --server-side. Пояснення нижче.
# Пінуємо версію, а не 'stable' — щоб на парі в усіх було однаково.
kubectl apply --server-side=true -n argocd -f \
  https://raw.githubusercontent.com/argoproj/argo-cd/v3.5.0/manifests/install.yaml
```

> ### ⚠️ Чому саме `--server-side`
> Без цього прапорця встановлення **впаде** ось так:
> ```
> The CustomResourceDefinition "applicationsets.argoproj.io" is invalid:
> metadata.annotations: Too long: may not be more than 262144 bytes
> ```
> Звичайний `kubectl apply` зберігає копію всього маніфесту в анотації
> `kubectl.kubernetes.io/last-applied-configuration`, а в етцд ліміт на
> анотацію — **256 КБ**. CRD `ApplicationSet` більший за цей ліміт.
>
> `--server-side` перекладає обчислення на API Server, і ця анотація не
> створюється взагалі. Помилка трапляється **вже після** створення частини
> ресурсів, тож просто перезапустіть команду з прапорцем — вона доліє решту.
>
> Якщо перевстановлюєте поверх невдалої спроби, додайте `--force-conflicts`.

Це 59 обʼєктів: 3 CRD, 6 Deployment, 1 StatefulSet і супровідні ролі.

```bash
# Чекаємо, поки всі 7 подів піднімуться (~2-3 хв)
kubectl wait --for=condition=available --timeout=300s \
  deployment --all -n argocd

kubectl get pods -n argocd
```

### 4.3. Заходимо в UI

```bash
# Пароль адміністратора згенеровано автоматично
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath="{.data.password}" | base64 -d; echo

# Тунель до UI (окремий термінал — тримати відкритим)
kubectl port-forward svc/argocd-server -n argocd 8080:443
```

Відкрийте **https://localhost:8080**

- Браузер попередить про самопідписаний сертифікат — це нормально,
  тисніть «Додатково» → «Перейти».
- Логін: `admin`, пароль — з команди вище.

### 4.4. Вказуємо ArgoCD на свій репозиторій

**Обовʼязковий крок.** У файлах `deploy/4-argocd/*.yaml` стоїть заглушка.

```bash
# Підставити свій URL автоматично (macOS)
export REPO=$(git remote get-url origin)
sed -i '' "s|https://github.com/alexnodejs/mds06-mlops-platform.git|$REPO|" \
  deploy/4-argocd/application-kustomize.yaml \
  deploy/4-argocd/application-helm.yaml

# Перевірити
grep repoURL deploy/4-argocd/*.yaml
```

> На Linux: `sed -i` без `''`.

### 4.5. Створюємо Application

```bash
kubectl apply -f deploy/4-argocd/application-kustomize.yaml
```

Розберемо ключові поля:

```yaml
metadata:
  namespace: argocd          # ⚠️ Application ЗАВЖДИ живе тут,
                             #    а не там, куди деплоїть
spec:
  source:
    repoURL: https://github.com/ваш/репозиторій.git
    targetRevision: HEAD     # гілка, тег або коміт
    path: deploy/3-kustomize/overlays/gitops
                             # ArgoCD сам побачить kustomization.yaml
                             # і зрозуміє, що це Kustomize

  destination:
    server: https://kubernetes.default.svc   # «цей самий кластер»
    namespace: demo-gitops

  syncPolicy:
    automated:
      prune: true            # видалили з Git -> видалити з кластера
      selfHeal: true         # ⭐ змінили руками -> повернути як у Git
```

Дивимось:

```bash
kubectl get application -n argocd
```

```
NAME           SYNC STATUS   HEALTH STATUS
nginx-gitops   Synced        Healthy
```

В UI ви побачите граф ресурсів: Application → Namespace, ConfigMap, Service,
Deployment → ReplicaSet → Pod.

```bash
kubectl port-forward -n demo-gitops svc/gitops-nginx-demo 8085:80
```

**http://localhost:8085** — 🟠 помаранчева сторінка. **Її ніхто не деплоїв
руками** — ArgoCD прочитав Git і застосував сам.

### 4.6. ⭐⭐ Демонстрація self-heal

Це найсильніша демонстрація заняття. Показує різницю між «застосував» і
«підтримує стан».

```bash
# Термінал 1 — спостерігаємо
kubectl get pods -n demo-gitops -w
```

```bash
# Термінал 2 — ламаємо руками
kubectl scale deployment gitops-nginx-demo -n demo-gitops --replicas=7
```

У першому терміналі: підніметься 7 подів... і **за кілька секунд ArgoCD
поверне 2**. Бо в Git написано 2.

Спробуйте видалити зовсім:

```bash
kubectl delete deployment gitops-nginx-demo -n demo-gitops
kubectl get deployment -n demo-gitops    # ArgoCD створить його заново
```

> **Порівняйте з ReplicaSet із Теми 5.** Там self-healing був на рівні
> **подів**: упав под — ReplicaSet підняв новий. Тут self-healing на рівні
> **конфігурації**: хтось змінив сам Deployment — ArgoCD повернув його до
> стану з Git. Це два різні рівні захисту.

У UI застосунок на мить стане `OutOfSync`, потім знову `Synced` — і в
історії залишиться слід. Це і є «audit» зі слайда 8.

### 4.7. ⭐ Демонстрація «змінив у Git → оновилось саме»

Працює, якщо ви пушите у **свій** репозиторій.

```bash
# 1. Змінюємо текст сторінки
sed -i '' 's|<h1>ArgoCD · GitOps</h1>|<h1>ArgoCD — оновлено з Git!</h1>|' \
  deploy/3-kustomize/overlays/gitops/index.html

# 2. Пушимо. Жодного kubectl!
git add deploy/3-kustomize/overlays/gitops/index.html
git commit -m "demo: змінюємо текст сторінки"
git push
```

Тепер чекаємо. **За замовчуванням ArgoCD опитує Git раз на 3 хвилини.**
Щоб не чекати на парі — натисніть **Refresh** в UI або:

```bash
kubectl annotate application nginx-gitops -n argocd \
  argocd.argoproj.io/refresh=hard --overwrite
```

Оновіть вкладку 8085 — текст змінився. **Ви жодного разу не звернулись
до кластера.** Єдиною дією був `git push`.

> У проді замість опитування налаштовують **webhook** із GitHub —
> тоді синхронізація стається за секунди після пушу.

### 4.8. Бонус: ArgoCD + Helm (тема наступного заняття)

Для ArgoCD не важливо, чим спаковано застосунок:

```bash
kubectl apply -f deploy/4-argocd/application-helm.yaml
kubectl get application -n argocd
kubectl port-forward -n demo-gitops-helm svc/nginx-demo 8086:80
```

**http://localhost:8086** — 🟣 фіолетова сторінка. Той самий Helm-чарт, що
в способі 2, але:

```yaml
source:
  path: deploy/2-helm/nginx-demo
  helm:
    parameters:
      - name: page.accent
        value: "#6A1B9A"
```

> **Важлива деталь:** ArgoCD виконує `helm template`, а **не** `helm install`.
> Тому `helm list -n demo-gitops-helm` буде порожнім — станом керує ArgoCD,
> а не Helm. Це нормально і саме так задумано.

---

## 5. Усі чотири поруч

Фінальний акорд заняття. Відкриваємо всі шість тунелів одночасно:

```bash
kubectl port-forward -n demo-kubectl     svc/nginx-demo        8081:80 &
kubectl port-forward -n demo-helm        svc/nginx-demo        8082:80 &
kubectl port-forward -n demo-dev         svc/dev-nginx-demo    8083:80 &
kubectl port-forward -n demo-prod        svc/prod-nginx-demo   8084:80 &
kubectl port-forward -n demo-gitops      svc/gitops-nginx-demo 8085:80 &
kubectl port-forward -n demo-gitops-helm svc/nginx-demo        8086:80 &

sleep 3
open http://localhost:808{1,2,3,4,5,6}   # macOS: відкриє шість вкладок
```

Зупинити всі тунелі:

```bash
kill %1 %2 %3 %4 %5 %6
# або грубо: pkill -f "kubectl port-forward"
```

Один застосунок. Шість сторінок. Чотири способи доставки.

```bash
# Подивитись усе разом
kubectl get pods -A -l app=nginx-demo
kubectl get ns -l lesson=topic-6
```

### Порівняння (слайд 14)

| Підхід | Що робить | Коли використовувати | Обмеження |
|---|---|---|---|
| `kubectl apply` | застосовує готові YAML | навчання, PoC, дебаг | немає параметризації |
| **Helm** | пакує ресурси в шаблонізований чарт | повторюваний деплой ML-сервісів | треба розуміти шаблони |
| **Kustomize** | накладає зміни на базові YAML | різні конфігурації середовищ | менша гнучкість за Helm |
| **CI/CD** | запускає deploy із пайплайну | автоматизація build-test-deploy | потрібні безпечні доступи до кластера |
| **GitOps** | синхронізує кластер зі станом у Git | production, аудит, контроль змін | вищий поріг впровадження |

**У реальних проєктах їх комбінують:** Helm + ArgoCD або Kustomize + ArgoCD.
Ви щойно зробили обидві комбінації.

### Що обрати на практиці

- **Один сервіс, одне середовище** → `kubectl apply`, не ускладнюйте.
- **Ставите чужий застосунок** (MLflow, Grafana, Postgres) → **Helm**,
  бо для них уже є готові чарти.
- **Свій застосунок у 2–4 середовищах** → **Kustomize**, він простіший.
- **Команда >2 людей або потрібен аудит** → **ArgoCD** поверх будь-чого
  з попереднього.

---

## 6. Прибирання

> Порядок важливий: **спочатку ArgoCD, потім решта.** Якщо видалити ресурси
> раніше за Application, ArgoCD побачить розбіжність із Git і **створить їх
> заново** — self-heal працює проти вас.

```bash
# 1. ArgoCD Applications. finalizer сам прибере створені ними ресурси.
kubectl delete -f deploy/4-argocd/application-helm.yaml --ignore-not-found
kubectl delete -f deploy/4-argocd/application-kustomize.yaml --ignore-not-found

# Дочекатись, поки зникнуть
kubectl get application -n argocd

# 2. Ручні деплої
kubectl delete -k deploy/3-kustomize/overlays/prod --ignore-not-found
kubectl delete -k deploy/3-kustomize/overlays/dev  --ignore-not-found
helm uninstall nginx-demo -n demo-helm
kubectl delete -f deploy/1-kubectl/ --ignore-not-found

# 3. Namespace, що лишились.
# demo-helm-prod з'явиться лише якщо ви робили необовʼязковий крок
# `helm install nginx-prod` — інакше --ignore-not-found просто промовчить.
kubectl delete ns demo-helm demo-helm-prod demo-gitops-helm --ignore-not-found

# 4. Сам ArgoCD (якщо більше не потрібен)
kubectl delete -n argocd -f \
  https://raw.githubusercontent.com/argoproj/argo-cd/v3.5.0/manifests/install.yaml
kubectl delete namespace argocd

# 5. Перевірка
kubectl get ns | grep -E "demo-|argocd"   # має бути порожньо
```

> ⚠️ **Не покладайтесь тут на `kubectl get ns -l lesson=topic-6`.**
> Namespace `demo-gitops-helm` створив сам ArgoCD через
> `CreateNamespace=true`, і мітки `lesson` на ньому **немає** — фільтр за
> міткою його не покаже, і ви вирішите, що прибрали все. Перевіряйте
> звичайним `grep`, як вище.

Далі — розділ 13 у [README.md](README.md#13--прибирання-і-контроль-витрат):
`terraform destroy`.

> Оскільки тут усі сервіси `ClusterIP`, жодного AWS Load Balancer не
> створено — і `terraform destroy` не буде заблокований. Але якщо ви
> міняли `type` на `LoadBalancer`, спершу видаліть ці Service.

---

## 7. Типові помилки

| Симптом | Причина | Як полагодити |
|---|---|---|
| Встановлення ArgoCD: `CustomResourceDefinition "applicationsets.argoproj.io" is invalid: metadata.annotations: Too long` | CRD більший за ліміт анотації в 256 КБ | `kubectl apply --server-side=true`; при повторі додайте `--force-conflicts` |
| `Warning: metadata.finalizers: prefer a domain-qualified finalizer name` | попередження, а не помилка | ігноруйте — це офіційний finalizer ArgoCD |
| `helm lint`: `mapping values are not allowed in this context` | двокрапка в тексті YAML без лапок (напр. `description: Тема 6: текст`) | взяти значення в лапки |
| `helm upgrade` пройшов, а сторінка стара | Kubernetes не рестартує поди при зміні ConfigMap | анотація `checksum/config` у шаблоні пода |
| `Error: INSTALLATION FAILED: ... already exists` | реліз із таким іменем уже є | `helm list -A`, тоді `helm upgrade` замість `install` |
| `helm template` показує правильно, а в кластері інше | застосували не той файл значень | `helm get values <реліз> -n <ns>` |
| Kustomize: `must build at directory` | вказали файл замість теки | `-k` приймає **теку** з `kustomization.yaml` |
| Kustomize: у Deployment зʼявився другий контейнер | у патчі помилка в `name:` контейнера | імʼя в патчі має точно збігатися з базовим |
| Kustomize: `security; file is not in or below the current directory` | `files:` посилається вище за теку kustomization | тримайте файли поруч із `kustomization.yaml` |
| ArgoCD: `repository not found` | не замінили `repoURL` або репозиторій приватний | `grep repoURL deploy/4-argocd/*.yaml`; зробіть репо публічним |
| ArgoCD: Application створився, але нічого не деплоїть | Application не в namespace `argocd` | `metadata.namespace: argocd` |
| ArgoCD: `ComparisonError: path does not exist` | шлях `path:` не існує в **запушеній** гілці | ви закомітили, але не запушили: `git push` |
| ArgoCD: зміни в Git не приїжджають | опитування раз на 3 хв | Refresh в UI або анотація `argocd.argoproj.io/refresh=hard` |
| Видалив Deployment, а він повернувся | `selfHeal: true` — так і має бути | видаляйте Application, а не ресурси |
| Видалив Application, ресурси лишились | немає `finalizers` | додати `resources-finalizer.argocd.argoproj.io` |
| Поди в `Pending` | ліміт подів (~17 на `t3.medium`) або немає ресурсів | `kubectl describe pod`; підніміть `node_desired_size` до 3 |
| ArgoCD UI: `ERR_EMPTY_RESPONSE` | ходите на `http://`, а треба `https://` | **https**://localhost:8080 |
| `port-forward` обірвався | под перестворився | просто запустіть команду знову |

---

## Що далі

- **Helm-чарти готових сервісів:** MLflow (Bitnami), Grafana, Prometheus
- **ArgoCD ApplicationSet** — одна декларація на десятки застосунків
- **Sync waves і hooks** — керування порядком деплою (спершу міграція БД, потім застосунок)
- **Sealed Secrets / External Secrets** — як тримати секрети в Git безпечно
- **Progressive delivery** — Argo Rollouts: canary і blue-green

## Посилання

| Ресурс | Посилання |
|---|---|
| Helm — документація | https://helm.sh/docs/ |
| Helm — вбудовані обʼєкти (`.Release`, `.Chart`) | https://helm.sh/docs/chart_template_guide/builtin_objects/ |
| Kustomize — довідник полів | https://kubectl.docs.kubernetes.io/references/kustomize/kustomization/ |
| ArgoCD — документація | https://argo-cd.readthedocs.io/ |
| ArgoCD — специфікація Application | https://argo-cd.readthedocs.io/en/stable/operator-manual/application.yaml |
| Artifact Hub — пошук чартів | https://artifacthub.io/ |

## Версії, на яких перевірено

| Компонент | Версія |
|---|---|
| Helm | 4.1.4 |
| Kustomize (у складі kubectl) | 5.7.1 |
| kubectl | 1.34.1 |
| ArgoCD | 3.5.0 |
| Kubernetes (EKS) | 1.34 |
