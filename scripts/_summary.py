"""Читає JSON-логи Job тренування і друкує людський підсумок.

Окремий файл, а не python3 -c у скрипті: всередині лапок bash будь-які
вкладені лапки доводиться екранувати, і рівно на цьому попередня версія
падала з SyntaxError після успішного тренування — тренування пройшло,
а студент бачив трейсбек.
"""
import json
import sys

for line in sys.stdin:
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        continue

    ev = d.get("event")
    if ev == "run_finished":
        p = d["params"]
        print("   n={:>4} depth={:>5}  accuracy={:.4f}  f1={:.4f}".format(
            p["n_estimators"], str(p["max_depth"]), d["accuracy"], d["f1"]))
    elif ev == "best_run":
        print("   ⭐ найкращий: f1={:.4f}".format(d["f1"]))
    elif ev == "registered":
        alias = d.get("alias") or "без аліаса (рішення за quality gate, Тема 10)"
        print("   📦 у реєстрі: {} v{} — {}".format(d["model"], d["version"], alias))
    elif ev == "uploaded":
        print("   {:<34} {:>5} рядків  sha {}".format(d["uri"], d["rows"], d["digest"]))
    elif ev == "bucket_created":
        print("   + створено бакет {}".format(d["bucket"]))
    elif ev == "dataset_loaded":
        print("   📂 дані: {} — {} рядків, digest {}".format(d["uri"], d["rows"], d["digest"]))
    elif ev == "dataset_unavailable":
        print("   ⚠️  сховище недоступне ({}) — падаю на load_iris()".format(d["uri"]))
    elif ev == "training_result":
        champ = d.get("champion_f1")
        print("   📊 попередній чемпіон: {}".format(
            "{:.4f}".format(champ) if champ is not None else "не було"))
