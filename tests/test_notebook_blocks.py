"""The notebook's blocks (frontend/src/blocks) and the Utilities
calculators (app/static/bench-calcs.js) are JavaScript; their arithmetic is
checked in Node, when Node is there."""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("node"), "needs Node")
class Blocks(unittest.TestCase):
    def check(self, name: str) -> None:
        r = subprocess.run(["node", str(ROOT / "tests" / "js" / name)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)

    def test_qpcr_leaves_control_wells_out_and_flags_one_that_amplified(self):
        self.check("qpcr.check.mjs")

    def test_protein_concentration_from_a280_and_the_sequence(self):
        self.check("protein.check.mjs")

    def test_the_utilities_calculators_give_known_answers(self):
        self.check("bench-calcs.check.mjs")

    def test_plate_standard_series_follows_the_selection_shape(self):
        self.check("plate.check.mjs")

    def test_step_timers_leave_time_points_alone(self):
        self.check("durations.check.mjs")

    def test_a_formulation_works_out_moles_and_equivalents_and_a_log_is_a_line(self):
        self.check("formulation.check.mjs")


if __name__ == "__main__":
    unittest.main()
