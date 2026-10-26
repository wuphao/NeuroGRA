import tempfile
import unittest
from pathlib import Path

from neurogra_kb import KBConfig, TextKnowledgeService
from neurogra_kg.runtime import Budget
from neurogra.clinical.config import ClinicalConfig
from neurogra.clinical.intake import ingest_patient
from neurogra.clinical.profiling import profile_case
from neurogra.clinical.schemas import RequestContent
from neurogra.clinical.patient_data import PatientDataService
from neurogra.clinical.main_tools import MainToolExecutor, RunModelBudget, BudgetedPort
from neurogra.clinical.storage import RunStore, BudgetExceeded
from neurogra.clinical.rwe import RweConfig, RweForm, RweFailure


class MainToolsTests(unittest.TestCase):
    def test_synthesis_budget_reservation_is_atomic_under_concurrency(self):
        from concurrent.futures import ThreadPoolExecutor
        run = self.store.create_run({}, {"llm": 8, "retrieval": 0, "image": 0, "seconds": 30})
        def reserve(_):
            try:
                return self.store.reserve(run, "llm", reserved=6)[0]
            except BudgetExceeded:
                return None
        with ThreadPoolExecutor(4) as pool:
            calls = list(pool.map(reserve, range(12)))
        self.assertEqual(sum(call is not None for call in calls), 2)
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = ClinicalConfig(project_root=self.root)
        self.snapshot = profile_case(ingest_patient({"患者ID": "p1", "病史": "未开展检查。",
            "年龄": 68}, self.config), [], self.config, None)
        self.store = RunStore(self.root / "run.sqlite")
        self.run = self.store.create_run(self.config.model_dump(mode="json"), self.config.budget.model_dump())

    def request(self, **kwargs):
        return RequestContent(type="patient_data", question="查询已有年龄资料", requested_fields=["年龄"],
                              reason="核实已有记录", **kwargs)

    def test_local_not_recorded_is_not_a_negative_result(self):
        patient = PatientDataService(self.snapshot, self.config)
        found = patient.fetch_patient_data(self.request(), "code_request")
        self.assertEqual(found.status, "found")
        missing = patient.fetch_patient_data(RequestContent(type="patient_data", question="教育资料",
            requested_fields=["教育年限"], reason="解释条件"), "code_request2")
        self.assertEqual(missing.status, "not_recorded")
        self.assertFalse(missing.observations)
        self.assertIn("教育年限", missing.missing_fields)

    def remote(self, client):
        cfg = RweConfig(project_id=8, forms=[RweForm(key="history", form_id=1, name="病史")])
        return PatientDataService(self.snapshot, self.config, rwe_config=cfg, rwe_client=client)

    def test_remote_patient_binding_is_verified_after_filter(self):
        class Client:
            def resolve_patient(self, value):
                return {"patient_number": "p1", "patient_id": 7}
            def fetch_form(self, form, patient):
                return [{"patient_id": 99, "病史": "错误患者记录"}]
        with self.assertRaisesRegex(ValueError, "binding"):
            self.remote(Client()).fetch_patient_data(self.request(form_key="history"), "code_request")

    def test_remote_form_allowlist_and_source_failure(self):
        class Client:
            def resolve_patient(self, value):
                return {"patient_number": "p1", "patient_id": 7}
            def fetch_form(self, form, patient):
                raise RweFailure("rwe_api_unavailable")
        service = self.remote(Client())
        self.assertEqual(service.fetch_patient_data(self.request(form_key="invented"), "id").status, "unsupported")
        self.assertEqual(service.fetch_patient_data(self.request(form_key="history"), "id2").status, "source_unavailable")

    def test_remote_selected_fields_keep_independent_raw_snapshot(self):
        import json
        from neurogra.clinical.utils import sha256_file
        class Client:
            def resolve_patient(self, value):
                return {"patient_number": "p1", "patient_id": 7}
            def fetch_form(self, form, patient):
                return [{"id": 10, "patient_id": 7, "年龄": 68, "教育年限": 9}]
        fetched = self.remote(Client()).fetch_patient_data(self.request(form_key="history"), "code_request")
        self.assertEqual(fetched.status, 'found')
        source = Path(fetched.source_path)
        self.assertEqual(sha256_file(source), fetched.source_checksum)
        raw = json.loads(source.read_text(encoding='utf-8'))
        self.assertEqual(raw['forms'][0]['records'][0]['教育年限'], 9)
        self.assertFalse(any(row.name == '教育年限' for row in fetched.observations))
        self.assertTrue(any(record.locator.get('rwe') for record in fetched.records))

    def test_main_receipts_replay_without_recharging_and_reject_unknown_refs(self):
        tool = MainToolExecutor(self.snapshot, self.config, self.store, self.run)
        result = tool.execute(self.request(), scope="main")
        before = self.store.summary(self.run)["calls"]
        self.assertEqual(result, tool.execute(self.request(), scope="main"))
        self.assertEqual(before, self.store.summary(self.run)["calls"])
        self.assertTrue(result["action_id"].startswith("action_"))
        with self.assertRaises(ValueError):
            tool.execute(self.request(source_refs=["other_patient"]), scope="main")

    def test_kb_background_query_and_source_expansion_without_graph(self):
        import json
        doc = self.root / "document.json"
        doc.write_text(json.dumps({"blocks": [{"kind": "paragraph", "page": 1,
                                               "text": "MMSE 认知评估原文，无图谱声明。"}]}), encoding="utf-8")
        kb = TextKnowledgeService(KBConfig(root=self.root))
        release = kb.publish([doc], vector=False)["release_id"]
        tools = MainToolExecutor(self.snapshot, self.config, self.store, self.run, text_service=kb)
        request = RequestContent(type="knowledge", question="MMSE", reason="查解释资料")
        result = tools.execute(request, scope="main")
        evidence = result["data"]["evidence"][0]
        self.assertIsNone(evidence["statement_id"])
        self.assertEqual(evidence["allowed_use"], "background")
        self.assertEqual(result["data"]["kb_release_id"], release)
        source = tools.execute(RequestContent(type="source_context", question="回读原文",
            source_refs=[evidence["span_id"]], reason="核对上下文"), scope="main")
        self.assertEqual(source["status"], "found")

    def test_internal_repairs_share_global_budget_and_reserve_synthesis(self):
        config = self.config.budget.model_copy(update={"llm": 8})
        run = self.store.create_run({}, config.model_dump())
        ledger = RunModelBudget(self.store, run, "sdk", reserve_synthesis=6)
        class Port:
            def execute(self, task, payload, schema, budget):
                budget.reserve("llm")
                budget.reserve("llm")  # Simulated format repair, still charged globally.
                return "repaired"
        sdk_budget = Budget(30)
        self.assertEqual(BudgetedPort(Port(), ledger).execute("L1", {}, None, sdk_budget), "repaired")
        with self.assertRaises(BudgetExceeded):
            BudgetedPort(Port(), ledger).execute("L1", {}, None, sdk_budget)
        self.assertEqual(sum(row["n"] for row in self.store.summary(run)["calls"]), 2)
        self.assertIsNone(sdk_budget.on_reserve)
