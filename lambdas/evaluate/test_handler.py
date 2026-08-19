"""Самоперевірка quality gate. Мережі не потребує:

    python lambdas/evaluate/test_handler.py

Перевіряється рівно те, що ламається найчастіше: розбір логів пода і
арифметика порогу. Решта пайплайну — виклики AWS, вони перевіряються
реальним прогоном.
"""
import json
from handler import handler


def logs(*lines: str):
    """Обгортка у структуру, яку віддає eks:runJob.sync."""
    return {"logs": {"pods": {"train-abc-x7k9p": {"containers": {"train": {"log": "\n".join(lines)}}}}}}


def result(**kw):
    base = {"event": "training_result", "model": "iris-rf", "version": 5,
            "run_id": "r1", "f1": 0.95, "accuracy": 0.95, "champion_f1": 0.90}
    base.update(kw)
    return json.dumps(base)


# ── краща за чинну -> промоут ──
out = handler(logs("шум, не JSON", result(f1=0.95, champion_f1=0.90)), None)
assert out["promote"] is True, out
assert abs(out["delta"] - 0.05) < 1e-9, out

# ── гірша -> відхилено, і саме Succeed-гілка, а не виняток ──
out = handler(logs(result(f1=0.88, champion_f1=0.93)), None)
assert out["promote"] is False, out
assert out["delta"] < 0, out

# ── однакова з точністю до шуму -> НЕ промоутити (інакше прод кочується даремно) ──
out = handler(logs(result(f1=0.9301, champion_f1=0.9300)), None)
assert out["promote"] is False, out

# ── чинної моделі ще немає -> перша стає чемпіоном ──
out = handler(logs(result(champion_f1=None)), None)
assert out["promote"] is True and out["champion_f1"] is None, out

# ── беремо ОСТАННЮ подію, а не першу ──
out = handler(logs(result(f1=0.5, champion_f1=0.9), result(f1=0.99, champion_f1=0.9)), None)
assert out["f1"] == 0.99, out

# ── зайві рядки MLflow і run_finished не збивають розбір ──
out = handler(logs(
    '{"event": "run_finished", "run_id": "x", "f1": 0.11, "params": {}}',
    "2026/08/19 10:00:00 INFO mlflow: Registered model",
    result(f1=0.97, champion_f1=0.90),
), None)
assert out["promote"] is True and out["version"] == "5", out

# ── логів немає взагалі -> ВИНЯТОК, а не тихе "не промоутити" ──
try:
    handler({"logs": {"pods": {}}}, None)
    raise AssertionError("мав кинути виняток")
except ValueError as e:
    assert "training_result" in str(e)

print("✅ quality gate: 7 перевірок пройдено")
