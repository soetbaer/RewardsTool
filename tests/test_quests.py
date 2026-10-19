"""Tests der Texterkennung für Events (python -m unittest discover tests)."""
import unittest
from datetime import datetime, timedelta

from rewards import quests
from rewards.quests import QuestTask

# Aufgaben der Halloween-Quest 2026 (Text wie auf der Rewards-Seite)
HALLOWEEN = [
    QuestTask("Dein Halloween-Abenteuer beginnt hier",
              "Aktiviere dein Halloween-Abenteuer und entdecke, welche Überraschungen dich erwarten.",
              "Quest aktivieren"),
    QuestTask("Bing hat deine Halloween-Antworten",
              "Suche an 5 von 10 Tagen auf deinem Desktop. (Die Suchvorgänge müssen mindestens 24 Stunden "
              "auseinanderliegen.) (0/5 Tage abgeschlossen)", "Suche starten", locked=True),
    QuestTask("Gönnen Sie Ihrem Desktop ein Halloween-Makeover",
              "Verleihen Sie Ihrem täglichen Scrollen einen gruseligen Touch, indem Sie ein "
              "Halloween-Hintergrundbild herunterladen.", "Halloween-Hintergrundbild herunterladen", locked=True),
    QuestTask("Verwirklichen Sie Ihre Halloween-Ideen mit KI",
              "Von gespenstischen Szenen bis zu unheimlichen Kreationen – entdecken Sie KI-Tools, die Ihre "
              "Fantasie ein wenig magischer machen können.", "Probieren Sie es aus", locked=True),
    QuestTask("Finden Sie Ihren Halloween-Look mit Bing",
              "Suchen Sie nach Kostümideen, angesagten Looks und kreativer Inspiration, um dieses Halloween "
              "unvergesslich zu machen.", "Kostüme suchen", locked=True),
    QuestTask("Mach deinen Raum Halloween-bereit",
              "Für Dekorationsideen, saisonale Inspiration und gruselige Akzente für jede Ecke, suchen Sie auf Bing.",
              "Finde Halloween-Dekoration", locked=True),
]
TOPICS = {"kostüm": ["{event} Kostüm Ideen"], "deko": ["{event} Deko Ideen"], "*": ["{event} Bräuche"]}


class Classify(unittest.TestCase):
    def test_halloween(self):
        kinds = [quests.classify(t) for t in HALLOWEEN]
        self.assertEqual(kinds, [quests.ACTIVATE, quests.MULTIDAY, quests.VISIT, quests.VISIT, quests.SEARCH,
                                 quests.SEARCH])

    def test_unknown(self):
        self.assertEqual(quests.classify(QuestTask("Sammle 3 Sticker", "Mach mit beim Sammelspaß", "Weiter")),
                         quests.UNKNOWN)

    def test_quiz(self):
        self.assertEqual(quests.classify(QuestTask("Gruseliges Quiz", "Teste dein Wissen", "Jetzt spielen")),
                         quests.QUIZ)

    def test_multiday_rule(self):
        rule = quests.multiday_rule(HALLOWEEN[1])
        self.assertEqual(rule, {"days": 5, "window": 10, "done": 0, "gap_hours": 24})
        self.assertEqual(quests.multiday_action(HALLOWEEN[1]), quests.SEARCH)
        self.assertIsNone(quests.multiday_rule(HALLOWEEN[0]))

    def test_multiday_english(self):
        t = QuestTask("Bing has answers", "Search on 5 out of 10 days. Searches must be 24 hours apart. (2/5 days)",
                      "Start searching")
        self.assertEqual(quests.multiday_rule(t), {"days": 5, "window": 10, "done": 2, "gap_hours": 24})


class Queries(unittest.TestCase):
    def test_event_name(self):
        self.assertEqual(quests.event_name(HALLOWEEN), "Halloween")

    def test_costume(self):
        q = quests.search_queries(HALLOWEEN[4], "Halloween", TOPICS)
        self.assertEqual(q[:3], ["Halloween Kostüm Ideen", "Halloween Kostüme", "Halloween Kostümideen"])

    def test_decoration(self):
        q = quests.search_queries(HALLOWEEN[5], "Halloween", TOPICS)
        self.assertEqual(q[:2], ["Halloween Deko Ideen", "Halloween-Dekoration"])

    def test_generic(self):
        self.assertEqual(quests.search_queries(HALLOWEEN[1], "Halloween", TOPICS), ["Halloween Bräuche"])
        self.assertEqual(quests.search_queries(HALLOWEEN[1], "", TOPICS), [])


class Multiday(unittest.TestCase):
    def test_wait(self):
        now = datetime(2026, 10, 20, 8, 0)
        self.assertEqual(quests.multiday_wait({}, now), 0)
        entry = {"times": [(now - timedelta(hours=23, minutes=50)).isoformat()]}
        self.assertAlmostEqual(quests.multiday_wait(entry, now), 15 * 60)
        entry = {"times": [(now - timedelta(hours=25)).isoformat()]}
        self.assertEqual(quests.multiday_wait(entry, now), 0)


class Config(unittest.TestCase):
    def test_defaults(self):
        cfg = quests.check_config(None)
        self.assertEqual(cfg["max_wait_minutes"], 45)
        self.assertTrue(cfg["enabled"])

    def test_bounds(self):
        for bad in (-1, 181, "10", 1.5, True):
            with self.assertRaises(ValueError):
                quests.check_config({"max_wait_minutes": bad})
        with self.assertRaises(ValueError):
            quests.check_config({"delay_seconds": [10, 5]})


if __name__ == "__main__":
    unittest.main()
