"""Run: python -m unittest discover -s tests -p test_startup.py."""
import asyncio
from functools import lru_cache
import sys
import threading
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from app.services import startup


class StartupTests(unittest.TestCase):
    def test_shutdown_does_not_wait_for_blocked_recovery(self):
        entered, release = threading.Event(), threading.Event()
        captured = []
        def recovery(*, stop):
            captured.append(stop)
            entered.set()
            release.wait(5)
        module = ModuleType("app.services.durable_workflows")
        module.recover_pending = recovery

        async def run():
            async with startup.lifespan(object()):
                for _ in range(100):
                    if entered.is_set():
                        break
                    await asyncio.sleep(.01)
                self.assertTrue(entered.is_set())
            self.assertTrue(captured[0].is_set())

        try:
            with patch.dict(sys.modules, {"app.services.durable_workflows": module}), \
                 patch.object(startup, "preload_models"):
                asyncio.run(asyncio.wait_for(run(), timeout=2))
        finally:
            release.set()

    def test_loads_all_getters_before_ready_and_reuses_caches(self):
        modules = {}
        constructors = []
        getters = []
        for module_name, getter_name in (
            ("app.services.chunk", "get_embedding_model"),
            ("app.services.search_services", "get_reranker"),
            ("app.services.context_management", "get_tokenizer"),
        ):
            module = ModuleType(module_name)
            constructor = Mock(return_value=object())
            getter = lru_cache(maxsize=1)(constructor)
            setattr(module, getter_name, getter)
            modules[module_name] = module
            constructors.append(constructor)
            getters.append(getter)

        async def run():
            async with startup.lifespan(None):
                for constructor, getter in zip(constructors, getters):
                    constructor.assert_called_once_with()
                    self.assertIs(getter(), constructor.return_value)
                    constructor.assert_called_once_with()

        with patch.dict(sys.modules, modules), patch.object(startup, "logger") as logger:
            asyncio.run(run())
            logger.info.assert_called_once_with("event=startup_models_ready")

    def test_load_failure_prevents_serving(self):
        entered = False

        async def run():
            nonlocal entered
            async with startup.lifespan(None):
                entered = True

        with patch.object(startup, "preload_models", side_effect=RuntimeError("load failed")):
            with self.assertRaisesRegex(RuntimeError, "load failed"):
                asyncio.run(run())
        self.assertFalse(entered)


if __name__ == "__main__":
    unittest.main()
