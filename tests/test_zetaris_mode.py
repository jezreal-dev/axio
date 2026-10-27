import unittest
from app.config import Settings
from app.adapters.zetaris import ZetarisAdapter, QueryError


class ZetarisModeTests(unittest.TestCase):
    def test_live_without_credentials_cannot_silently_use_sqlite(self):
        adapter = ZetarisAdapter(Settings(sample_mode=False, zetaris_api_key="", zetaris_rest_url=""))
        self.assertEqual(adapter.backend, "unconfigured")
        with self.assertRaises(QueryError):
            adapter.query("SELECT 1", "test")
        self.assertFalse(adapter.health()["reachable"])

    def test_sample_mode_remains_explicit_even_with_credentials(self):
        adapter = ZetarisAdapter(Settings(sample_mode=True, zetaris_api_key="test", zetaris_rest_url="http://localhost:8888/api/v1.0"))
        self.assertEqual(adapter.backend, "sqlite-federation")
