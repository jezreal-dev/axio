"""Comprehensive automated test suite for IncidentWhy."""
from __future__ import annotations

import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from app.agents.orchestrator import run_investigation
from app.config import get_settings
from run import IncidentWhyHTTPHandler

ROOT_DIR = Path(__file__).resolve().parent.parent


class TestIncidentWhyCore(unittest.TestCase):
    def test_scenario_01_deploy_red_herring(self) -> None:
        res = run_investigation(
            {"scenario_id": "scenario_01_deploy_red_herring"},
            echo_logs=False,
        )
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["rounds_completed"], 2)
        self.assertEqual(res["verified_root_cause"]["suspected_change_id"], "flg-502")
        falsified_ids = [h["suspected_change_id"] for h in res["falsified_hypotheses"]]
        self.assertEqual(falsified_ids, ["dep-1005", "cfg-301"])
        self.assertEqual(res["collaboration"]["sql_schema_recoveries"], 1)
        self.assertAlmostEqual(res["financial_impact"]["deduplicated_lost_revenue_usd"], 10732.46, places=2)
        self.assertGreaterEqual(len(res["verified_root_cause"]["citations"]), 3)

    def test_scenario_02_rate_limit_churn(self) -> None:
        res = run_investigation(
            {"scenario_id": "scenario_02_rate_limit_churn"},
            echo_logs=False,
        )
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["rounds_completed"], 2)
        self.assertEqual(res["verified_root_cause"]["suspected_change_id"], "cfg-302")
        falsified_ids = [h["suspected_change_id"] for h in res["falsified_hypotheses"]]
        self.assertEqual(falsified_ids, ["tkt-7003", "dep-1007"])
        # Verify contract ledger falsification reason mentions 0 price changes
        tkt_reason = res["falsified_hypotheses"][0]["why_falsified"]
        self.assertIn("Contract & Ledger Contradiction", tkt_reason)
        self.assertGreater(res["financial_impact"]["deduplicated_lost_revenue_usd"], 100000.0)

    def test_scenario_03_third_party_timeout_leak_and_retry_audit(self) -> None:
        res = run_investigation(
            {"scenario_id": "scenario_03_third_party_timeout_leak"},
            echo_logs=False,
        )
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["rounds_completed"], 2)
        self.assertEqual(res["verified_root_cause"]["suspected_change_id"], "upstream-taxly-eu-504")
        falsified_ids = [h["suspected_change_id"] for h in res["falsified_hypotheses"]]
        self.assertEqual(falsified_ids, ["flg-505", "dep-1011"])
        # Verify Evaluator caught retry inflation (gross > deduplicated)
        fin = res["financial_impact"]
        self.assertAlmostEqual(fin["deduplicated_lost_revenue_usd"], 9084.40, places=2)
        self.assertAlmostEqual(fin["gross_failed_volume_usd"], 24415.46, places=2)
        self.assertGreater(fin["retry_inflation_factor"], 2.0)

    def test_meterless_world_model_and_hmem_persistence(self) -> None:
        cfg = get_settings()
        res = run_investigation({"scenario_id": "scenario_01_deploy_red_herring"}, echo_logs=False)
        met = res["sponsor_technology"]["meterless"]
        self.assertGreaterEqual(met["world_model"]["entity_count"], 5)
        self.assertGreaterEqual(met["hmem"]["memory_count"], 4)
        self.assertIn("wired_services", met["context_builder_manifest"])
        self.assertTrue((cfg.state_dir / "meterless_world_model.json").exists())
        self.assertTrue((cfg.state_dir / "meterless_hmem.json").exists())


class TestIncidentWhyHTTPServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), IncidentWhyHTTPHandler)
        cls.port = cls.server.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def test_health_and_inspection_endpoints(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/health") as r:
            self.assertEqual(r.status, 200)
            data = json.loads(r.read().decode("utf-8"))
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["zetaris"]["status"], "ok")

        with urllib.request.urlopen(f"{self.base_url}/api/scenarios") as r:
            self.assertEqual(r.status, 200)
            scen = json.loads(r.read().decode("utf-8"))
            self.assertEqual(len(scen["scenarios"]), 3)

        with urllib.request.urlopen(f"{self.base_url}/api/world-model") as r:
            self.assertEqual(r.status, 200)
            wm = json.loads(r.read().decode("utf-8"))
            self.assertIn("state", wm)

        with urllib.request.urlopen(f"{self.base_url}/api/memory") as r:
            self.assertEqual(r.status, 200)
            mem = json.loads(r.read().decode("utf-8"))
            self.assertIn("state", mem)

        with urllib.request.urlopen(f"{self.base_url}/") as r:
            self.assertEqual(r.status, 200)
            html = r.read().decode("utf-8")
            self.assertIn("Axio", html)

        with urllib.request.urlopen(f"{self.base_url}/slides") as r:
            self.assertEqual(r.status, 200)
            slides_html = r.read().decode("utf-8")
            self.assertIn("Axio", slides_html)
            self.assertIn("Slide 8 of 8", slides_html)

    def test_post_run_endpoint(self) -> None:
        payload = json.loads((ROOT_DIR / "input_examples" / "incident_01_checkout_drop.json").read_text(encoding="utf-8"))
        req = urllib.request.Request(
            f"{self.base_url}/run",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.status, 200)
            res = json.loads(r.read().decode("utf-8"))
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["verified_root_cause"]["suspected_change_id"], "flg-502")


if __name__ == "__main__":
    unittest.main()
