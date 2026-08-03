import importlib.util
import pathlib
import unittest


SCRIPT = pathlib.Path(__file__).parents[1] / "scripts" / "quick_screen.py"
SPEC = importlib.util.spec_from_file_location("quick_screen", SCRIPT)
quick_screen = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(quick_screen)


class QuickScreenTests(unittest.TestCase):
    def test_five_year_average_dividend_yield(self):
        years = ["2021", "2022", "2023", "2024", "2025"]
        per10 = {yy: 10 for yy in years}
        avg, annual = quick_screen.average_dividend_yield(
            per10, 20, years
        )
        self.assertEqual(avg, 5.0)
        self.assertEqual(set(annual), set(years))

    def test_missing_dividend_is_zero_in_full_window(self):
        years = ["2021", "2022", "2023"]
        avg, annual = quick_screen.average_dividend_yield(
            {"2021": 10}, 20, years
        )
        self.assertEqual(avg, 1.67)
        self.assertEqual(annual["2022"], 0.0)
        self.assertEqual(annual["2023"], 0.0)


if __name__ == "__main__":
    unittest.main()
