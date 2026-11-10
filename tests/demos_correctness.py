"""Check actual executable results against independent Python calculations."""
import bisect
from pathlib import Path
import subprocess
import sys
import unittest

ARRAY, SEARCH, MATRIX = map(lambda path: str(Path(path).resolve()), sys.argv[1:4])
del sys.argv[1:4]


def run(executable, *args):
    process = subprocess.run([executable, *map(str, args)], capture_output=True, text=True, timeout=10)
    if process.returncode:
        raise AssertionError(process.stderr)
    return dict(part.split("=", 1) for part in process.stdout.split())


class DemoTests(unittest.TestCase):
    def test_both_array_orders_visit_all_elements(self):
        elements = 1024 * 1024 // 8
        passes = 3
        expected = passes * elements * (elements + 1) // 2 + passes * (passes - 1) // 2
        for mode in ("sequential", "strided"):
            with self.subTest(mode=mode):
                result = run(ARRAY, 1, passes, mode)
                self.assertEqual(int(result["checksum"]), expected)
                self.assertEqual(result["mode"], mode)

    def test_binary_search_matches_python_bisect(self):
        for elements in (1, 2, 7, 16):
            with self.subTest(elements=elements):
                data = list(range(0, elements * 2, 2))
                state, hits, checksum = 42, 0, 0
                for _ in range(200):
                    state = (state * 6364136223846793005 + 1442695040888963407) % (1 << 64)
                    key = (state >> 32) % (elements * 2)
                    position = bisect.bisect_left(data, key)
                    if position < len(data) and data[position] == key:
                        hits += 1
                        checksum += position + 1
                result = run(SEARCH, elements, 200)
                self.assertEqual(int(result["hits"]), hits)
                self.assertEqual(int(result["checksum"]), checksum)

    def test_matrix_orders_match_reference_product(self):
        for size in (1, 3, 5, 8):
            with self.subTest(size=size):
                a = [[((i * size + j) * 3 + 1) % 17 for j in range(size)] for i in range(size)]
                b = [[((i * size + j) * 7 + 2) % 19 for j in range(size)] for i in range(size)]
                expected = 0
                for repeat in range(2):
                    a[0][0] = (repeat + 1) % 17
                    product = [[sum(x * y for x, y in zip(row, column)) for column in zip(*b)] for row in a]
                    expected += sum(map(sum, product))
                for mode in ("rows", "columns"):
                    result = run(MATRIX, size, 2, mode)
                    self.assertEqual(int(result["checksum"]), expected)

    def test_invalid_arguments_and_help(self):
        cases = [
            (ARRAY, ["0"]), (ARRAY, ["1x"]), (ARRAY, ["513"]), (ARRAY, ["1", "1", "bad"]),
            (SEARCH, ["-1"]), (SEARCH, ["1", "0"]), (SEARCH, ["1", "1", "extra"]),
            (MATRIX, ["1025"]), (MATRIX, ["1", "0"]), (MATRIX, ["1", "1", "bad"]),
        ]
        for executable, arguments in cases:
            with self.subTest(executable=executable, arguments=arguments):
                process = subprocess.run([executable, *arguments], capture_output=True, text=True, timeout=3)
                self.assertNotEqual(process.returncode, 0)
                self.assertTrue(process.stderr)
        for executable in (ARRAY, SEARCH, MATRIX):
            process = subprocess.run([executable, "--help"], capture_output=True, text=True, timeout=3)
            self.assertEqual(process.returncode, 0)
            self.assertTrue(process.stdout)


if __name__ == "__main__":
    unittest.main()
