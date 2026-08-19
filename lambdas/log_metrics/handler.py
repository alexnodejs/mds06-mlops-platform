"""LogMetrics — фіксація статусу прогону (слайд 26, lambda/log_metrics.py;
крок 6 «Фіксація статусу» зі слайда 8).

Метрики моделі вже лежать у MLflow. Навіщо дублювати їх у CloudWatch:
MLflow відповідає на питання «яка модель краща», CloudWatch — на питання
«чи здоровий сам пайплайн». Це різні читачі. Алерт «третій прогін поспіль
нічого не промоутить» ставиться на CloudWatch, і для нього не потрібно, щоб
MLflow був піднятий.

ВХІД — результат кроку EvaluateModel плюс контекст виконання.
ВИХІД — короткий підсумок, який видно в консолі Step Functions без розгортання
вкладених обʼєктів.
"""

import os

import boto3

NAMESPACE = os.getenv("METRIC_NAMESPACE", "MDS06/Training")
cloudwatch = boto3.client("cloudwatch")


def handler(event, _context):
    ev = event.get("evaluation") or {}
    params = event.get("params") or {}

    promoted = bool(ev.get("promote"))
    # Вимір experiment, а НЕ commit_sha чи run_id: у CloudWatch кожна
    # унікальна комбінація вимірів — окрема метрика, за яку йде оплата.
    # Вимір зі значенням, що не повторюється, дає нескінченну кардинальність
    # і графік з однієї точки на кожній лінії. Та сама пастка, що з мітками
    # у Prometheus (Тема 8).
    dims = [{"Name": "experiment", "Value": params.get("experiment", "unknown")}]

    data = [
        {"MetricName": "F1", "Value": float(ev.get("f1", 0)), "Unit": "None", "Dimensions": dims},
        {"MetricName": "Accuracy", "Value": float(ev.get("accuracy", 0)), "Unit": "None", "Dimensions": dims},
        {"MetricName": "Promoted", "Value": 1.0 if promoted else 0.0, "Unit": "Count", "Dimensions": dims},
    ]
    if ev.get("delta") is not None:
        data.append({"MetricName": "F1Delta", "Value": float(ev["delta"]), "Unit": "None", "Dimensions": dims})

    cloudwatch.put_metric_data(Namespace=NAMESPACE, MetricData=data)

    return {
        "status": "ПРОМОУТ" if promoted else "ВІДХИЛЕНО",
        "model_version": ev.get("version"),
        "f1": ev.get("f1"),
        "champion_f1": ev.get("champion_f1"),
        "reason": ev.get("reason"),
        "commit_sha": params.get("short_sha"),
        "metrics_namespace": NAMESPACE,
    }
