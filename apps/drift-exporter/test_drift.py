"""Самоперевірка експортера: без Loki, без кластера, без мережі.

Запуск:  python test_drift.py   (жодного pytest — це один прогін assert-ів)

Перевіряє те, що реально може зламатись:
  1) розбір відповіді Loki (сміття в стрімі не мусить валити парсер);
  2) що KS-тест МОВЧИТЬ на нормальних даних і СПРАЦЬОВУЄ на зсунутих;
  3) що всі 6 контрактних метрик є в /metrics із правильними іменами;
  4) що /simulate-drift справді роняє p-value, а /healthz відповідає.
"""

import json
import os
import random
import urllib.request

# ⚠️ ДО імпорту drift_exporter: DATASET_URI читається на рівні модуля, і з
# непорожнім значенням load_reference() пішов би в MinIO, якого в тесті немає.
# Порожнє значення = вбудований load_iris(), тобто той самий еталон, на якому
# зміряні всі числа нижче. Самоперевірка мусить лишатись offline.
os.environ["DATASET_URI"] = ""

from prometheus_client import REGISTRY  # noqa: E402

from drift_exporter import (
    FEATURES,
    SIM,
    _train_split,
    check_drift,
    load_reference,
    parse_streams,
    serve,
    simulate_window,
)

# Порт для HTTP-частини самоперевірки. 0 = ядро дає ВІЛЬНИЙ порт, і це
# дефолт навмисно: усередині пода на 9100 уже слухає сам експортер, тож
# serve() на тому ж порту падає з OSError "Address already in use" — рівно
# там, де `kubectl exec deploy/drift-exporter -- python test_drift.py`
# найпотрібніший. Явний номер (TEST_PORT=9101) потрібен хіба щоб постукати
# в тестовий сервер збоку.
TEST_PORT = int(os.getenv("TEST_PORT", "0"))

CONTRACT = (
    "drift_detected",
    "drift_p_value",
    "drift_check_timestamp_seconds",
    "prediction_class_share",
    "reference_dataset_size",
    "current_window_size",
)


def loki_response(n, shift=0.0, jitter=0.05, seed=1):
    """Ліпить відповідь Loki у тому ж форматі, що й справжня, з тими самими
    полями, які пише app.py Теми 8. Семплить ТІ САМІ рядки Iris, що й
    виправлений loadgen — інакше тест перевіряв би розподіл, якого в
    кластері не буває."""
    rnd = random.Random(seed)
    X, y, names = _train_split()
    rows = list(zip(X.tolist(), y.tolist()))
    values = []
    for _ in range(n):
        row, label = rnd.choice(rows)
        feats = {f: round(max(0.1, rnd.gauss(v + shift, jitter)), 2) for f, v in zip(FEATURES, row)}
        values.append(["1755000000000000000", json.dumps({
            "ts": "2026-08-17T10:00:00+0000", "level": "INFO", "event": "predict",
            "input": feats, "prediction": names[label], "confidence": 0.99,
        })])
    # У реальному стрімі трапляється сміття від uvicorn і чужі події —
    # парсер мусить це пережити, а не впасти.
    values.append(["1755000000000000001", "INFO:     Started server process [1]"])
    values.append(["1755000000000000002", json.dumps({"event": "validation_error"})])
    return {"data": {"result": [{"stream": {"app": "ml-model"}, "values": values}]}}


def gauge(name, **labels):
    return REGISTRY.get_sample_value(name, labels or None)


def get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return r.read().decode()


def main():
    reference, ref_shares = load_reference()
    assert gauge("reference_dataset_size") == 120
    assert abs(sum(ref_shares.values()) - 1.0) < 1e-9
    assert set(ref_shares) == {"setosa", "versicolor", "virginica"}, ref_shares

    # -- сміття й порожня відповідь не ламають парсер
    cur, preds = parse_streams(loki_response(50))
    assert len(cur["sepal_length"]) == 50, len(cur["sepal_length"])
    assert len(preds) == 50
    assert parse_streams({}) == ({f: [] for f in FEATURES}, [])

    # -- замале вікно: нічого не рахуємо і НЕ скидаємо drift_detected
    cur, preds = parse_streams(loki_response(5))
    assert check_drift(reference, ref_shares, cur, preds) is False
    assert gauge("current_window_size") == 5
    assert gauge("drift_p_value", feature="sepal_length") is None, "не мусило рахувати"

    # -- нормальні дані: дріфту немає
    cur, preds = parse_streams(loki_response(400, shift=0.0))
    assert check_drift(reference, ref_shares, cur, preds) is True
    for f in FEATURES:
        assert gauge("drift_detected", feature=f) == 0.0, f"хибна тривога на чистих даних: {f}"
    normal_p = gauge("drift_p_value", feature="petal_length")

    # -- зсунуті дані: дріфт мусить спрацювати на ВСІХ ознаках
    cur, preds = parse_streams(loki_response(400, shift=0.8, seed=2))
    assert check_drift(reference, ref_shares, cur, preds) is True
    for f in FEATURES:
        assert gauge("drift_detected", feature=f) == 1.0, f"дріфт не помічено: {f}"
        assert gauge("drift_p_value", feature=f) < 0.01
    drift_p = gauge("drift_p_value", feature="petal_length")
    assert drift_p < normal_p, (drift_p, normal_p)

    # -- частки класів завжди дають 1.0 у сумі
    total = sum(gauge("prediction_class_share", **{"class": c}) for c in ref_shares)
    assert abs(total - 1.0) < 1e-9, total
    assert gauge("drift_check_timestamp_seconds") > 1_700_000_000

    # -- чужий лейбл у логах НЕ мусить валити хі-квадрат: саме на ньому суми
    #    спостережених і очікуваних розходились і scipy кидав ValueError,
    #    тобто под падав у CrashLoopBackOff від одного зайвого рядка в Loki
    cur, preds = parse_streams(loki_response(400))
    preds[0] = "iris-setosa-v2"
    assert check_drift(reference, ref_shares, cur, preds) is True
    total = sum(gauge("prediction_class_share", **{"class": c}) for c in ref_shares)
    assert abs(total - 1.0) < 1e-9, total

    # -- мало передбачень (очікувана частота < 5, правило Кокрена): p-value
    #    прогнозів лишається СТАРИМ, а не оновлюється шумом
    before = gauge("drift_p_value", feature="prediction")
    assert check_drift(reference, ref_shares, cur, ["setosa"] * 10) is True
    assert gauge("drift_p_value", feature="prediction") == before

    # -- симуляція: при shift=0 дріфту немає ЗА ПОБУДОВОЮ, при 0.8 — є всюди
    cur, preds = simulate_window(0.0, samples=400, seed=7)
    assert check_drift(reference, ref_shares, cur, preds) is True
    assert sum(gauge("drift_detected", feature=f) for f in FEATURES) == 0.0
    cur, preds = simulate_window(0.8, samples=400, seed=7)
    assert check_drift(reference, ref_shares, cur, preds) is True
    assert sum(gauge("drift_detected", feature=f) for f in FEATURES) == 4.0
    # зсув мусить перекосити ще й розподіл класів (prediction drift)
    assert gauge("prediction_class_share", **{"class": "setosa"}) < 0.1

    # -- HTTP: усі контрактні імена в /metrics, /healthz, /simulate-drift
    port = serve(TEST_PORT).server_port
    metrics = get(port, "/metrics")
    for name in CONTRACT:
        assert name in metrics, f"немає метрики {name} у /metrics"
    assert json.loads(get(port, "/healthz"))["status"] == "ok"

    SIM["shift"] = 0.0
    off = json.loads(get(port, "/simulate-drift?shift=0.8"))
    assert off["source"] == "simulation"
    assert all(v == 1.0 for v in off["drift_detected"].values()), off["drift_detected"]
    assert max(off["p_value"].values()) < 0.01, off["p_value"]

    print(f"ok: p чисте={normal_p:.3f} -> p зсунуте={drift_p:.2e}; "
          f"через /simulate-drift p={max(off['p_value'].values()):.2e}")


if __name__ == "__main__":
    main()
