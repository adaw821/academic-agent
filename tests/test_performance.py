"""Standard-library tests: python -m unittest discover -s tests -p test_performance.py."""
import asyncio
import inspect
import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.services import performance as perf


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "nested" / "performance.log"
        self.handlers = perf.logger.handlers[:]
        perf.logger.handlers = []
        with patch.dict("os.environ", {"PERFORMANCE_LOG_FILE": str(self.path)}):
            perf.configure_performance_logging()
            perf.configure_performance_logging()

    def tearDown(self):
        for handler in perf.logger.handlers:
            handler.close()
        perf.logger.handlers = self.handlers
        self.temp.cleanup()

    def records(self):
        return [dict(part.split("=", 1) for part in line.split() if "=" in part)
                for line in self.path.read_text(encoding="utf-8").splitlines()]

    def test_nested_failure_and_context_restore(self):
        with patch.object(perf, "perf_counter", side_effect=[1, 2, 3, 5]):
            with self.assertRaises(ValueError):
                with perf.measure_stage("outer", document_id=3):
                    with perf.measure_stage("inner"):
                        raise ValueError("private payload")
        rows = self.records()
        finished = [r for r in rows if r["event"] == "pipeline_stage_finished"]
        self.assertEqual([r["elapsed_ms"] for r in finished], ["1000.00", "4000.00"])
        self.assertEqual({r["status"] for r in finished}, {"error"})
        self.assertEqual(len({r["trace_id"] for r in rows}), 1)
        self.assertEqual(rows[1]["parent_span_id"], rows[0]["span_id"])
        self.assertEqual(rows[1]["document_id"], "3")
        self.assertIsNone(perf._context.get())
        self.assertNotIn("private payload", self.path.read_text(encoding="utf-8"))
        self.assertEqual(len(perf.logger.handlers), 1)

    def test_async_requests_are_isolated(self):
        @perf.timed_stage("request")
        async def request(project_id):
            await asyncio.sleep(0)
            with perf.measure_stage("child"):
                await asyncio.sleep(0)
            return project_id

        async def run():
            return await asyncio.gather(request(1), request(2))

        self.assertEqual(asyncio.run(run()), [1, 2])
        by_project = {}
        for row in self.records():
            by_project.setdefault(row["project_id"], set()).add(row["trace_id"])
        self.assertEqual([len(ids) for ids in by_project.values()], [1, 1])
        self.assertNotEqual(by_project["1"], by_project["2"])
        self.assertEqual(str(inspect.signature(request)), "(project_id)")

    def test_sync_result_and_append(self):
        @perf.timed_stage("sync")
        def task(document_id):
            return document_id

        self.assertEqual(task(4), 4)
        first = self.path.read_text(encoding="utf-8")
        self.assertEqual(task(5), 5)
        self.assertTrue(self.path.read_text(encoding="utf-8").startswith(first))
        self.assertEqual(len(self.records()), 4)


if __name__ == "__main__":
    unittest.main()
