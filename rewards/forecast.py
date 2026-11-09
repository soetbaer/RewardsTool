"""Prognose: Tage bis zum Einlöse-Ziel (bzw. bis zum nächsten Vielfachen) aus dem Tagesschnitt der Punkte.

Grundlage sind die Punktestände aus dem Laufverlauf (vor und nach jedem Lauf) plus der zuletzt gelesene Stand.
Verdient ist die Summe der Zuwächse zwischen aufeinanderfolgenden Ständen; Rückgänge (Einlösen) zählen nicht.
Der Schnitt läuft über die letzten höchstens WINDOW_DAYS Kalendertage, bei kürzerem Verlauf über den ganzen Verlauf.
"""
import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path

WINDOW_DAYS = 30


def _parse_time(value) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def readings_from_history(lines: list[str]) -> list[tuple[datetime, int]]:
    """Punktestände (Zeitpunkt, Punkte) aus den Zeilen von history.jsonl."""
    out = []
    for line in lines:
        try:
            run = json.loads(line)
        except ValueError:
            continue
        if not isinstance(run, dict):
            continue
        for time_key, points_key in (("start", "points_before"), ("end", "points_after")):
            t, p = _parse_time(run.get(time_key)), run.get(points_key)
            if t and isinstance(p, int) and not isinstance(p, bool):
                out.append((t, p))
    return out


def daily_average(readings: list[tuple[datetime, int]], today: date | None = None,
                  window_days: int = WINDOW_DAYS) -> dict | None:
    """Verdiente Punkte pro Tag über die letzten höchstens window_days Tage.

    Rückgabe {"per_day": float, "days": int} oder None, wenn es weniger als zwei Stände gibt.
    Gezählt werden die Kalendertage vom ersten bis zum letzten Stand im Fenster (einschließlich) –
    ein Tag, an dem noch kein Lauf war, drückt den Schnitt also nicht."""
    today = today or date.today()
    first_day = today - timedelta(days=window_days - 1)
    points = [(t, p) for t, p in sorted(readings) if first_day <= t.date() <= today]
    if len(points) < 2:
        return None
    earned = sum(max(0, b[1] - a[1]) for a, b in zip(points, points[1:]))
    days = (points[-1][0].date() - points[0][0].date()).days + 1
    return {"per_day": earned / days, "days": days}


def goal_forecast(balance: int | None, price: int, per_day: float | None) -> dict | None:
    """Nächste Stufe des Ziels und die Tage bis dahin (aufgerundet).

    multiple = 1: Ziel noch nicht erreicht; 2, 3, …: nächstes Vielfache des Preises.
    days = None, wenn kein Schnitt vorliegt oder nichts verdient wurde."""
    if not price or price <= 0 or balance is None or balance < 0:
        return None
    multiple = balance // price + 1
    missing = multiple * price - balance
    days = math.ceil(missing / per_day) if per_day and per_day > 0 else None
    return {"multiple": multiple, "missing": missing, "days": days}


def load_readings(history_file: Path, status: dict | None) -> list[tuple[datetime, int]]:
    try:
        lines = history_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    readings = readings_from_history(lines)
    if status and status.get("logged_in") and isinstance(status.get("points"), int):
        t = _parse_time(status.get("updated"))
        if t:
            readings.append((t, status["points"]))
    return readings


def forecast_for_status(history_file: Path, status: dict | None) -> dict | None:
    """Prognose zum gespeicherten Status (für Webinterface und CLI); None ohne Ziel."""
    goal = (status or {}).get("goal")
    if not goal:
        return None
    avg = daily_average(load_readings(history_file, status))
    balance = status.get("points") if isinstance(status.get("points"), int) else goal.get("points")
    fc = goal_forecast(balance, goal.get("price") or 0, avg["per_day"] if avg else None)
    if fc:
        fc["per_day"] = round(avg["per_day"], 1) if avg else None
        fc["avg_days"] = avg["days"] if avg else 0
        fc["text"] = describe(fc)
    return fc


def describe(fc: dict | None) -> str:
    """Kurzer Text für Webinterface und CLI, z. B. 'Noch 12 Tage bis zum Ziel (Ø 150 Punkte/Tag)'."""
    if not fc:
        return ""
    target = "zum Ziel" if fc["multiple"] == 1 else f"x{fc['multiple']}"
    if fc["days"] is None:
        why = "noch kein Verlauf" if not fc["avg_days"] else "zuletzt keine Punkte verdient"
        return f"Tage bis {target}: keine Prognose ({why})"
    unit = "Tag" if fc["days"] == 1 else "Tage"
    basis = f"{fc['avg_days']} Tag" if fc["avg_days"] == 1 else f"{fc['avg_days']} Tage"
    return f"Noch {fc['days']} {unit} bis {target} (Ø {fc['per_day']:.0f} Punkte/Tag, letzte {basis})"
