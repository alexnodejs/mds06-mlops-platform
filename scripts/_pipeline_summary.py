"""Друкує підсумок виконання Step Functions по його вихідному JSON.

Окремий файл, а не python3 -c у скрипті: усередині лапок bash вкладені лапки
доводиться екранувати, і на цьому вже один раз падав scripts/train.sh —
робота проходила, а користувач бачив SyntaxError.
"""
import json
import sys

d = json.load(sys.stdin)
summary = d.get("summary", {})
ev = d.get("evaluation", {})

status = summary.get("status", "?")
mark = "✅" if status == "ПРОМОУТ" else "⛔"
print(f"   {mark} {status}  —  {ev.get('reason', '')}")

f1 = summary.get("f1")
version = summary.get("model_version")
if isinstance(f1, (int, float)):
    print(f"      версія моделі: {version}   f1: {f1:.4f}")
else:
    print(f"      версія моделі: {version}")

# Відхилена модель ЛИШАЄТЬСЯ в реєстрі — просто без аліаса. Це не сміття:
# до неї можна повернутись, порівняти й побачити, чому gate її не пустив.
if status != "ПРОМОУТ":
    print(f"      у проді лишилась чинна модель, реєстр поповнився версією {version}")
