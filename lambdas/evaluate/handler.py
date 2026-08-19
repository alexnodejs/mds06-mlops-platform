"""EvaluateModel — quality gate: нова модель краща за чинну чи ні.

ЧОМУ ЦЕЙ КРОК — LAMBDA, А НЕ JOB У КЛАСТЕРІ.
MLflow живе всередині кластера за ClusterIP-сервісом, і Lambda ззовні до нього
не достукається (ClusterIP — віртуальна адреса, яку маршрутизує kube-proxy
лише на нодах; не допоможе навіть Lambda у тому самому VPC). Тому ми НЕ
ходимо в MLflow звідси взагалі. Замість цього тренувальний под сам друкує
підсумок останнім рядком stdout, а Step Functions віддає нам ці логи —
і Lambda лишається чистою функцією без мережі й без прав.

Це і є причина, чому train.py логує подію training_result у стабільному
форматі: вона — контракт між кластером і пайплайном.

ВХІД — вихід кроку eks:runJob.sync (структура з документації Step Functions):
    {"logs": {"pods": {"train-abc-x7k9p": {"containers": {"train": {"log": "...stdout..."}}}}}}

ВИХІД:
    {"promote": true/false, "reason": "...", "version": "5",
     "f1": 0.966, "champion_f1": 0.933, "delta": 0.033}
"""

import json
import os

# Наскільки нова модель має бути кращою, щоб її пускати в прод.
# 0.0 означало б, що будь-яке коливання в четвертому знаку (а воно є навіть
# при однакових даних, бо RandomForest використовує випадковість) вважається
# покращенням, і прод перекочувався б на кожному запуску без користі.
MIN_DELTA = float(os.getenv("MIN_DELTA", "0.001"))


def _extract_log(event) -> str:
    """Дістає stdout контейнера з відповіді eks:runJob.sync.

    Форма — logs.pods.<ім'я пода>.containers.<ім'я контейнера>.log. Імена
    невідомі наперед (под отримує випадковий суфікс), тому обходимо словники,
    а не звертаємось за ключем.

    Забір логів у Step Functions — best-effort: при помилці замість поля
    "log" приходять "error" і "cause", і таска НЕ падає. Тому порожній
    результат тут — очікуваний випадок, а не аварія.
    """
    parts = []
    pods = (event.get("logs") or {}).get("pods") or {}
    for pod in pods.values():
        for container in (pod.get("containers") or {}).values():
            if "log" in container:
                parts.append(container["log"])
    return "\n".join(parts)


def _last_event(log: str, name: str):
    """Останнє входження JSON-події з таким полем event.

    Саме ОСТАННЄ: train.py друкує run_finished для кожного запуску сітки,
    а training_result — один раз наприкінці. Брати перше входження було б
    помилкою, якби формат колись змінився.
    """
    found = None
    for line in log.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if data.get("event") == name:
            found = data
    return found


def handler(event, _context):
    log = _extract_log(event)
    result = _last_event(log, "training_result")

    if result is None:
        # Job завершився успішно, але контракту не виконав. Це помилка коду,
        # а не моделі, тому виняток, а не "promote: false": мовчазний відкат
        # приховав би зламаний train.py на кілька занять.
        raise ValueError(
            "у логах немає події training_result — train.py відпрацював не до кінця "
            "або з логів забрали замало рядків (LogOptions.tailLines)"
        )

    f1 = float(result["f1"])
    champion = result.get("champion_f1")

    if champion is None:
        # Першої моделі ще немає — порівнювати нема з чим, вона й стає чинною.
        return {"promote": True, "reason": "чинної моделі ще немає — ця стає першою",
                "version": str(result["version"]), "f1": f1,
                "champion_f1": None, "delta": None,
                "accuracy": float(result.get("accuracy", 0)),
                "run_id": result.get("run_id", "")}

    champion = float(champion)
    delta = f1 - champion
    promote = delta >= MIN_DELTA

    reason = (
        f"f1 {f1:.4f} проти {champion:.4f} у чинної: приріст {delta:+.4f} "
        f"{'≥' if promote else '<'} поріг {MIN_DELTA}"
    )
    return {"promote": promote, "reason": reason,
            "version": str(result["version"]), "f1": f1,
            "champion_f1": champion, "delta": round(delta, 6),
            "accuracy": float(result.get("accuracy", 0)),
            "run_id": result.get("run_id", "")}
