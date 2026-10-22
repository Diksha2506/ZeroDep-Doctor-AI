import unittest
from zerodep.scoring_engine import calculate_score

class TestScoringEngine(unittest.TestCase):
    def test_perfect_score_zero_dependencies(self):
        sb = calculate_score(
            declared_deps=set(),
            used_deps=set(),
            replaceable_deps={"requests"},
            total_stdlib_calls=10,
            total_calls=10
        )
        self.assertEqual(sb.current_score, 100.0)
        self.assertEqual(sb.potential_score, 100.0)
        self.assertEqual(sb.grade, "A+")

    def test_unused_dependency_penalty(self):
        sb = calculate_score(
            declared_deps={"requests", "pytz"},
            used_deps={"requests"},
            replaceable_deps={"requests"},
            total_stdlib_calls=5,
            total_calls=5
        )
        self.assertEqual(sb.unused_count, 1)
        self.assertEqual(sb.unused_penalty, 15.0)

    def test_replaceable_dependency_penalty(self):
        sb = calculate_score(
            declared_deps={"requests"},
            used_deps={"requests"},
            replaceable_deps={"requests"},
            total_stdlib_calls=5,
            total_calls=5
        )
        self.assertEqual(sb.replaceable_count, 1)
        self.assertEqual(sb.replaceable_penalty, 8.0)
        self.assertEqual(sb.potential_score, 100.0)  # If migrated, back to 100.0!

    def test_essential_dependency_penalty(self):
        sb = calculate_score(
            declared_deps={"numpy"},
            used_deps={"numpy"},
            replaceable_deps={"requests"},
            total_stdlib_calls=5,
            total_calls=5
        )
        self.assertEqual(sb.essential_count, 1)
        self.assertEqual(sb.essential_penalty, 4.0)

    def test_score_boundaries_0_to_100(self):
        # Extremely bad project
        sb = calculate_score(
            declared_deps={f"pkg_{i}" for i in range(20)},
            used_deps=set(),
            replaceable_deps=set(),
            total_stdlib_calls=0,
            total_calls=10
        )
        self.assertGreaterEqual(sb.current_score, 0.0)
        self.assertLessEqual(sb.current_score, 100.0)

if __name__ == "__main__":
    unittest.main()
