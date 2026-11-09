"""Tests der Prognose 'Noch X Tage bis zum Ziel' (python -m unittest discover tests)."""
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from rewards import forecast

TODAY = date(2026, 11, 9)


def at(days_ago: int, hour: int = 8) -> datetime:
    return datetime.combine(TODAY - timedelta(days=days_ago), datetime.min.time()).replace(hour=hour)


class DailyAverageTest(unittest.TestCase):
    def test_no_or_single_reading(self):
        self.assertIsNone(forecast.daily_average([], TODAY))
        self.assertIsNone(forecast.daily_average([(at(0), 1000)], TODAY))

    def test_short_history_uses_whole_history(self):
        # 3 Tage Verlauf, je 100 Punkte pro Tag (inkl. Punkte zwischen den Läufen, z. B. vom Handy)
        r = [(at(2), 1000), (at(2, 9), 1080), (at(1), 1100), (at(1, 9), 1180), (at(0), 1200), (at(0, 9), 1300)]
        avg = forecast.daily_average(r, TODAY)
        self.assertEqual(avg["days"], 3)
        self.assertAlmostEqual(avg["per_day"], 100)

    def test_window_is_capped_at_30_days(self):
        r = [(at(d), 10_000 - d * 50) for d in range(60)]  # 50 Punkte pro Tag über 60 Tage
        r.append((at(40, 9), 0))  # alter Ausreißer außerhalb des Fensters zählt nicht
        avg = forecast.daily_average(r, TODAY)
        self.assertEqual(avg["days"], 30)
        self.assertAlmostEqual(avg["per_day"], 29 * 50 / 30)

    def test_redeeming_does_not_count_negative(self):
        r = [(at(1), 5000), (at(1, 9), 5200), (at(0), 200), (at(0, 9), 400)]
        avg = forecast.daily_average(r, TODAY)
        self.assertAlmostEqual(avg["per_day"], 200)


class GoalForecastTest(unittest.TestCase):
    def test_goal_not_reached_rounds_up(self):
        fc = forecast.goal_forecast(1000, 2000, 300)
        self.assertEqual((fc["multiple"], fc["missing"], fc["days"]), (1, 1000, 4))

    def test_next_multiple_after_goal(self):
        self.assertEqual(forecast.goal_forecast(2000, 2000, 100)["multiple"], 2)
        fc = forecast.goal_forecast(6500, 2000, 100)
        self.assertEqual((fc["multiple"], fc["missing"], fc["days"]), (4, 1500, 15))

    def test_no_average(self):
        self.assertIsNone(forecast.goal_forecast(1000, 2000, None)["days"])
        self.assertIsNone(forecast.goal_forecast(1000, 2000, 0)["days"])
        self.assertIsNone(forecast.goal_forecast(1000, 0, 100))

    def test_describe(self):
        self.assertEqual(forecast.describe({"multiple": 1, "days": 4, "per_day": 300, "avg_days": 12}),
                         "Noch 4 Tage bis zum Ziel (Ø 300 Punkte/Tag, letzte 12 Tage)")
        self.assertEqual(forecast.describe({"multiple": 3, "days": 1, "per_day": 150.4, "avg_days": 30}),
                         "Noch 1 Tag bis x3 (Ø 150 Punkte/Tag, letzte 30 Tage)")
        self.assertIn("noch kein Verlauf",
                      forecast.describe({"multiple": 1, "days": None, "per_day": None, "avg_days": 0}))


class ForecastForStatusTest(unittest.TestCase):
    def test_from_history_file(self):
        now = datetime.now().replace(microsecond=0)
        runs = [{"start": (now - timedelta(days=d)).isoformat(), "end": (now - timedelta(days=d)).isoformat(),
                 "points_before": 1000 + (4 - d) * 100, "points_after": 1000 + (4 - d) * 100 + 100}
                for d in range(4, 0, -1)]
        runs.append({"start": now.isoformat(), "end": now.isoformat(), "result": "nicht eingeloggt"})
        with tempfile.TemporaryDirectory() as tmp:
            hist = Path(tmp) / "history.jsonl"
            hist.write_text("\n".join(json.dumps(r) for r in runs) + "\nkaputt\n", encoding="utf-8")
            status = {"logged_in": True, "points": 1400, "updated": now.isoformat(),
                      "goal": {"price": 2000, "points": 1400}}
            fc = forecast.forecast_for_status(hist, status)
        self.assertEqual(fc["multiple"], 1)
        self.assertEqual(fc["avg_days"], 5)
        self.assertEqual(fc["days"], 8)  # 600 fehlen, 400 Punkte in 5 Tagen = 80/Tag -> 7,5 -> 8
        self.assertTrue(fc["text"].startswith("Noch 8 Tage bis zum Ziel"))

    def test_without_goal(self):
        self.assertIsNone(forecast.forecast_for_status(Path("/nonexistent"), {"points": 5}))
        self.assertIsNone(forecast.forecast_for_status(Path("/nonexistent"), None))


if __name__ == "__main__":
    unittest.main()
