"""Тренування моделі Iris. Запускається ОДИН РАЗ під час `docker build`.

Навіщо тренувати на збірці, а не в рантаймі: под у кластері не має гарантованого
доступу до мережі й не має PVC (сховище кластера зламане), тому завантажувати
чи зберігати модель у рантаймі ніде. Датасет Iris вбудований у sklearn —
жодного походу в інтернет навіть на етапі збірки.
"""

import json
import pickle
import time
from pathlib import Path

import sklearn
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split

MODEL_PATH = Path(__file__).with_name("model.pkl")


def main() -> None:
    data = load_iris()

    # stratify=y — щоб у тесті були всі три класи (150 рядків, по 50 на клас).
    X_train, X_test, y_train, y_test = train_test_split(
        data.data, data.target, test_size=0.2, random_state=42, stratify=data.target
    )

    # random_state зафіксовано: без нього кожна збірка образу давала б іншу
    # модель, і теґ v1 означав би щоразу різні предбачення.
    clf = RandomForestClassifier(n_estimators=100, random_state=42)
    clf.fit(X_train, y_train)

    accuracy = float(accuracy_score(y_test, clf.predict(X_test)))

    # Кладемо модель і метадані в ОДИН pickle: у рантаймі достатньо одного
    # відкриття файлу, і метадані фізично не можуть розʼїхатися з вагами.
    bundle = {
        "model": clf,
        "classes": list(data.target_names),  # ['setosa','versicolor','virginica']
        "features": list(data.feature_names),  # порядок ознак — контракт з app.py
        "accuracy": accuracy,
        "model_type": type(clf).__name__,
        "sklearn_version": sklearn.__version__,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S+0000", time.gmtime()),
    }
    with MODEL_PATH.open("wb") as f:
        pickle.dump(bundle, f)

    # Лог у stdout — видно прямо в логах `docker build`, якщо збірка зламається.
    print(json.dumps({k: v for k, v in bundle.items() if k != "model"}, ensure_ascii=False))
    print(f"saved: {MODEL_PATH} ({MODEL_PATH.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
