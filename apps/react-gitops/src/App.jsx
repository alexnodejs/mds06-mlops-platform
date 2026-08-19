import { useState, useEffect } from "react";

// Дані, які згенерував entrypoint-скрипт при старті контейнера.
// Запасні значення потрібні для `npm run dev` — там config.js немає.
const INFO = window.APP_INFO ?? {
  version: "local",
  pod: "локальний запуск",
  node: "—",
  namespace: "—",
};

export default function App() {
  // useState — доводить студентам, що це справжній React, а не статичний HTML.
  const [clicks, setClicks] = useState(0);
  const [uptime, setUptime] = useState(0);

  // Лічильник секунд від моменту відкриття сторінки.
  useEffect(() => {
    const id = setInterval(() => setUptime((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <main>
      <p className="eyebrow">MDS06 · GitOps · окремий репозиторій</p>

      <h1>
        React у Kubernetes
        <span className="badge">{INFO.version}</span>
      </h1>

      <p className="lead">
        Цю сторінку зібрано з React, запаковано в образ, покладено в ECR і
        задеплоєно <strong>ArgoCD</strong> — прямо з Git. Ніхто не робив{" "}
        <code>kubectl apply</code>.
      </p>

      {/*
        Найцікавіше для демонстрації: імʼя пода.

        ⚠️ Оновлення сторінки імʼя НЕ змінить: `kubectl port-forward svc/...`
        прокидає тунель до ОДНОГО конкретного пода і тримається його.
        Балансування Service тут не працює — див. підказку внизу сторінки.

        А от коли ArgoCD відновить убитий под — імʼя зміниться.
      */}
      <dl className="facts">
        <div>
          <dt>Под</dt>
          <dd className="mono">{INFO.pod}</dd>
        </div>
        <div>
          <dt>Нода</dt>
          <dd className="mono">{INFO.node}</dd>
        </div>
        <div>
          <dt>Namespace</dt>
          <dd className="mono">{INFO.namespace}</dd>
        </div>
        <div>
          <dt>Версія образу</dt>
          <dd className="mono">{INFO.version}</dd>
        </div>
      </dl>

      <div className="interactive">
        <button onClick={() => setClicks((c) => c + 1)}>
          Натиснуто {clicks}{" "}
          {clicks === 1 ? "раз" : clicks >= 2 && clicks <= 4 ? "рази" : "разів"}
        </button>
        <span className="uptime">
          сторінка відкрита {uptime} с — стан живе у браузері, не в поді
        </span>
      </div>

      <p className="hint">
        Через <code>port-forward</code> ви завжди бачите <strong>один і той
        самий</strong> под — тунель прив&apos;язується до нього. Щоб побачити
        балансування Service, треба запит <em>зсередини</em> кластера:
        <br />
        <code>
          kubectl run c --rm -it --image=curlimages/curl --restart=Never -- sh
          -c &apos;for i in $(seq 6); do curl -s react-app.demo-react/config.js
          | grep pod:; done&apos;
        </code>
      </p>
    </main>
  );
}
