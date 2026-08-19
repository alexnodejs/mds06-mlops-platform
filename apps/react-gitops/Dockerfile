# ══════════════════════════════════════════════════════════════════════════
# ЕТАП 1 — збірка. Тут є Node, npm і всі 200 МБ node_modules.
# ══════════════════════════════════════════════════════════════════════════
FROM node:22-alpine AS build

WORKDIR /app

# Спершу копіюємо ЛИШЕ маніфести залежностей, а не весь код.
# Docker кешує кожен шар: доки package*.json не змінився, крок `npm ci`
# береться з кешу. Змінили App.jsx — залежності НЕ перевстановлюються.
# Скопіювали б увесь код одразу — npm ci виконувався б при кожній правці.
COPY package.json package-lock.json ./

# npm ci, а не npm install: ставить рівно те, що в package-lock.json,
# і падає, якщо лок розійшовся з package.json. Саме те, що треба для CI.
RUN npm ci

COPY . .

# Версію передаємо ззовні: --build-arg APP_VERSION=v1
ARG APP_VERSION=dev
RUN npm run build

# ══════════════════════════════════════════════════════════════════════════
# ЕТАП 2 — виконання. Node тут уже НЕ потрібен: React зібрався у статику.
# ══════════════════════════════════════════════════════════════════════════
FROM nginx:1.29-alpine

# ARG живе лише в межах свого етапу, тому оголошуємо вдруге
# і зберігаємо в ENV — щоб значення поїхало разом з образом
# і його побачив entrypoint-скрипт.
ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION

# Забираємо з першого етапу ТІЛЬКИ теку dist. node_modules лишаються там.
# Підсумковий образ ~50 МБ замість ~400 МБ.
COPY --from=build /app/dist /usr/share/nginx/html

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.d/40-runtime-config.sh /docker-entrypoint.d/40-runtime-config.sh

# ── Готуємо запуск від НЕпривілейованого користувача ──────────────────────
# Контейнер від root — зайвий ризик: пролом у nginx дає root у контейнері.
# Користувач nginx (uid 101) уже є в базовому образі, треба лише роздати права.
RUN chmod +x /docker-entrypoint.d/40-runtime-config.sh \
    && chown -R nginx:nginx /usr/share/nginx/html /var/cache/nginx \
    && touch /var/run/nginx.pid \
    && chown nginx:nginx /var/run/nginx.pid

USER nginx

# 8080, а не 80: непривілейований користувач не має права слухати порти < 1024.
EXPOSE 8080
