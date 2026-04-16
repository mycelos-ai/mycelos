"""End-to-End Integration Test: Invoice Scanner Agent.

Tests the ENTIRE Mycelos stack:
  User Request → Planner (needs_new_agent) → Creator Pipeline
  (Gherkin → Tests → Code → Sandbox → Audit) → Registration → Execution

Uses mocked LLM calls but REAL test execution (pytest in subprocess).
Test PDFs are simulated as text files with invoice-like content.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mycelos.agents.agent_spec import AgentSpec, classify_effort
from mycelos.agents.creator_pipeline import CreatorPipeline
from mycelos.agents.planner_context import build_planner_context
from mycelos.app import App
from mycelos.chat.service import ChatService


@pytest.fixture
def app() -> App:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MYCELOS_MASTER_KEY"] = "test-key-invoice-e2e"
        a = App(Path(tmp))
        a.initialize()
        yield a


@pytest.fixture
def invoice_dir(tmp_path: Path) -> Path:
    """Create a test invoices directory with mock PDF-like files."""
    invoices = tmp_path / "invoices"
    invoices.mkdir()

    # Create mock "PDF" files (text content simulating extracted PDF text)
    (invoices / "rechnung-001.txt").write_text(
        "Rechnung RE-2026-001\nFirma Alpha GmbH\nDatum: 2026-01-15\nBetrag: 1250.00 EUR"
    )
    (invoices / "rechnung-002.txt").write_text(
        "Rechnung RE-2026-042\nBeta GmbH\nDatum: 2026-02-20\nBetrag: 890.50 EUR"
    )
    (invoices / "rechnung-003.txt").write_text(
        "Rechnung RE-2026-099\nGamma AG\nDatum: 2026-03-10\nBetrag: 3400.00 EUR"
    )
    return invoices


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    out = tmp_path / "output"
    out.mkdir()
    return out


# --- Mock LLM responses that produce VALID, WORKING code ---

INVOICE_GHERKIN = """\
Feature: Invoice Scanner
  Scannt einen Ordner nach Rechnungsdateien und extrahiert die Daten.

  Scenario: Rechnungsdateien im Ordner erkennen
    Given ein Ordner mit Rechnungsdateien
    When der Agent den Ordner scannt
    Then soll er alle .txt Dateien finden

  Scenario: Rechnungsdaten extrahieren
    Given eine Rechnungsdatei mit strukturiertem Text
    When der Agent die Datei liest
    Then soll er Rechnungsnummer, Empfaenger, Datum und Betrag extrahieren

  Scenario: Daten in CSV schreiben
    Given extrahierte Rechnungsdaten
    When der Agent die CSV erstellt
    Then soll die CSV einen Header und eine Zeile pro Rechnung enthalten

  Scenario: Leerer Ordner
    Given ein leerer Ordner
    When der Agent den Ordner scannt
    Then soll eine leere CSV mit nur Header erstellt werden
"""

INVOICE_TESTS = """\
import os
import tempfile
from pathlib import Path
from agent_code import InvoiceScanner

def _make_invoices(tmp_dir):
    Path(tmp_dir, "r1.txt").write_text(
        "Rechnung RE-001\\nFirma A\\nDatum: 2026-01-01\\nBetrag: 100.00 EUR"
    )
    Path(tmp_dir, "r2.txt").write_text(
        "Rechnung RE-002\\nFirma B\\nDatum: 2026-02-01\\nBetrag: 200.00 EUR"
    )

def test_scan_finds_files():
    agent = InvoiceScanner()
    with tempfile.TemporaryDirectory() as tmp:
        _make_invoices(tmp)
        inp = type('I', (), {'task': 'scan', 'context': {'input_dir': tmp, 'output_dir': tmp}})()
        result = agent.execute(inp)
        assert result.success

def test_extracts_invoice_data():
    agent = InvoiceScanner()
    with tempfile.TemporaryDirectory() as tmp:
        _make_invoices(tmp)
        inp = type('I', (), {'task': 'scan', 'context': {'input_dir': tmp, 'output_dir': tmp}})()
        result = agent.execute(inp)
        assert result.success
        assert isinstance(result.result, list)
        assert len(result.result) == 2

def test_csv_output():
    agent = InvoiceScanner()
    with tempfile.TemporaryDirectory() as tmp:
        _make_invoices(tmp)
        out = Path(tmp) / "output"
        out.mkdir()
        inp = type('I', (), {'task': 'scan', 'context': {'input_dir': tmp, 'output_dir': str(out)}})()
        result = agent.execute(inp)
        csv_files = list(out.glob("*.csv"))
        assert len(csv_files) == 1
        content = csv_files[0].read_text()
        assert "RE-001" in content
        assert "RE-002" in content

def test_empty_dir():
    agent = InvoiceScanner()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "output"
        out.mkdir()
        inp = type('I', (), {'task': 'scan', 'context': {'input_dir': tmp, 'output_dir': str(out)}})()
        result = agent.execute(inp)
        assert result.success
        assert result.result == []
"""

INVOICE_CODE = """\
import csv
import re
from pathlib import Path

class InvoiceScanner:
    agent_id = "invoice-scanner"
    agent_type = "deterministic"
    capabilities_required = ["filesystem.read"]

    def execute(self, input):
        input_dir = input.context.get("input_dir", ".")
        output_dir = input.context.get("output_dir", ".")

        try:
            invoices = self._scan_directory(input_dir)
            if output_dir:
                self._write_csv(invoices, output_dir)

            return type('Result', (), {
                'success': True,
                'result': invoices,
                'artifacts': [],
                'metadata': {'count': len(invoices)},
                'error': '',
            })()
        except Exception as e:
            return type('Result', (), {
                'success': False,
                'result': None,
                'artifacts': [],
                'metadata': {},
                'error': str(e),
            })()

    def _scan_directory(self, dir_path):
        invoices = []
        for f in sorted(Path(dir_path).glob("*.txt")):
            data = self._extract_data(f.read_text())
            if data:
                invoices.append(data)
        return invoices

    def _extract_data(self, text):
        result = {}
        # Extract invoice number
        m = re.search(r'Rechnung\\s+(RE-[\\w-]+)', text)
        if m:
            result['invoice_number'] = m.group(1)
        # Extract recipient (line after Rechnung)
        lines = text.strip().split('\\n')
        if len(lines) >= 2:
            result['recipient'] = lines[1].strip()
        # Extract date
        m = re.search(r'Datum:\\s*(\\d{4}-\\d{2}-\\d{2})', text)
        if m:
            result['date'] = m.group(1)
        # Extract amount
        m = re.search(r'Betrag:\\s*([\\d.]+)', text)
        if m:
            result['amount'] = float(m.group(1))
        return result if result else None

    def _write_csv(self, invoices, output_dir):
        if not invoices:
            # Write header-only CSV
            csv_path = Path(output_dir) / "invoices.csv"
            csv_path.write_text("invoice_number,recipient,date,amount\\n")
            return

        csv_path = Path(output_dir) / "invoices.csv"
        with open(csv_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['invoice_number', 'recipient', 'date', 'amount'])
            writer.writeheader()
            writer.writerows(invoices)
"""


def _mock_llm(*responses: str) -> MagicMock:
    mock = MagicMock()
    mock.total_tokens = 0
    all_responses = ("trivial",) + responses  # Prepend for classify_effort
    idx = [0]
    def side_effect(*args, **kwargs):
        i = min(idx[0], len(all_responses) - 1)
        idx[0] += 1
        mock.total_tokens += 100
        r = MagicMock()
        r.content = all_responses[i]
        r.total_tokens = 100
        r.model = "test"
        r.tool_calls = None
        return r
    mock.complete.side_effect = side_effect
    return mock


def _approve_audit() -> MagicMock:
    m = MagicMock()
    m.review_code_and_tests.return_value = {"approved": True, "findings": []}
    return m


# =========================================================================
# Phase 1: Feasibility
# =========================================================================


class TestFeasibility:

    def test_invoice_scanner_classified_as_medium(self):
        spec = AgentSpec(
            name="invoice-scanner",
            description="Scan invoice folder, extract PDF data, write CSV",
            capabilities_needed=["filesystem.read"],
        )
        assert classify_effort(spec) in ("trivial", "small", "medium")

    def test_planner_context_shows_no_matching_agent(self, app):
        ctx = build_planner_context(app)
        agent_ids = [a["id"] for a in ctx["available_agents"]]
        assert "invoice-scanner" not in agent_ids


# =========================================================================
# Phase 2: Creator Pipeline — Agent wird gebaut
# =========================================================================


class TestCreatorPipeline:

    def test_full_pipeline_creates_invoice_agent(self, app):
        """Creator Pipeline should produce a working invoice scanner agent."""
        spec = AgentSpec(
            name="invoice-scanner",
            description="Scan folder for invoices, extract data, write CSV",
            capabilities_needed=["filesystem.read"],
        )

        app._llm = _mock_llm(INVOICE_GHERKIN, INVOICE_TESTS, INVOICE_CODE)
        app._auditor = _approve_audit()

        pipeline = CreatorPipeline(app)
        result = pipeline.run(spec)

        assert result.success, f"Pipeline failed: {result.error}"
        assert result.agent_id == "invoice-scanner"

    def test_agent_registered_after_creation(self, app):
        spec = AgentSpec(name="invoice-scanner", description="Scan invoices",
                         capabilities_needed=["filesystem.read"])
        app._llm = _mock_llm(INVOICE_GHERKIN, INVOICE_TESTS, INVOICE_CODE)
        app._auditor = _approve_audit()

        CreatorPipeline(app).run(spec)

        agent = app.agent_registry.get("invoice-scanner")
        assert agent is not None
        assert agent["status"] == "active"

    def test_code_stored_in_object_store(self, app):
        spec = AgentSpec(name="invoice-scanner", description="Scan invoices",
                         capabilities_needed=["filesystem.read"])
        app._llm = _mock_llm(INVOICE_GHERKIN, INVOICE_TESTS, INVOICE_CODE)
        app._auditor = _approve_audit()

        CreatorPipeline(app).run(spec)

        agent = app.agent_registry.get("invoice-scanner")
        assert agent["code_hash"] is not None

        from mycelos.storage.object_store import ObjectStore
        store = ObjectStore(app.data_dir)
        code = store.load(agent["code_hash"])
        assert "InvoiceScanner" in code

    def test_gherkin_scenarios_generated(self, app):
        spec = AgentSpec(name="invoice-scanner", description="Scan invoices",
                         capabilities_needed=["filesystem.read"])
        app._llm = _mock_llm(INVOICE_GHERKIN, INVOICE_TESTS, INVOICE_CODE)
        app._auditor = _approve_audit()

        result = CreatorPipeline(app).run(spec)
        assert "Scenario" in result.gherkin
        assert "Rechnungsdaten" in result.gherkin or "Invoice" in result.gherkin

    def test_config_generation_created(self, app):
        spec = AgentSpec(name="invoice-scanner", description="Scan invoices",
                         capabilities_needed=["filesystem.read"])
        app._llm = _mock_llm(INVOICE_GHERKIN, INVOICE_TESTS, INVOICE_CODE)
        app._auditor = _approve_audit()

        gen_before = app.config.get_active_generation_id()
        CreatorPipeline(app).run(spec)
        gen_after = app.config.get_active_generation_id()
        assert gen_after != gen_before


# =========================================================================
# Phase 3: Generated code actually works on test data
# =========================================================================


class TestGeneratedCodeExecution:
    """Tests that the generated invoice scanner code actually works."""

    def test_code_passes_generated_tests(self):
        """The mock code should pass the mock tests (real pytest execution)."""
        from mycelos.agents.test_runner import run_agent_tests
        result = run_agent_tests(INVOICE_CODE, INVOICE_TESTS, timeout=30)
        assert result.passed, f"Tests failed:\n{result.output}\n{result.error}"
        assert result.tests_run >= 4

    def test_code_extracts_invoice_data(self, invoice_dir):
        """Run the invoice scanner on real test files."""
        # Write code to temp file and import it
        import importlib.util
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            code_path = Path(tmp) / "agent_code.py"
            code_path.write_text(INVOICE_CODE)

            spec = importlib.util.spec_from_file_location("agent_code", code_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            agent = mod.InvoiceScanner()
            inp = type('Input', (), {
                'task': 'scan',
                'context': {'input_dir': str(invoice_dir), 'output_dir': str(invoice_dir)},
            })()

            result = agent.execute(inp)
            assert result.success
            assert len(result.result) == 3

            # Check extracted data
            numbers = {r["invoice_number"] for r in result.result}
            assert "RE-2026-001" in numbers
            assert "RE-2026-042" in numbers
            assert "RE-2026-099" in numbers

    def test_csv_output_correct(self, invoice_dir, output_dir):
        """Verify CSV output contains all invoice data."""
        import importlib.util
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            code_path = Path(tmp) / "agent_code.py"
            code_path.write_text(INVOICE_CODE)

            spec = importlib.util.spec_from_file_location("agent_code", code_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            agent = mod.InvoiceScanner()
            inp = type('Input', (), {
                'task': 'scan',
                'context': {'input_dir': str(invoice_dir), 'output_dir': str(output_dir)},
            })()

            agent.execute(inp)

            csv_files = list(output_dir.glob("*.csv"))
            assert len(csv_files) == 1

            content = csv_files[0].read_text()
            assert "invoice_number" in content  # Header
            assert "RE-2026-001" in content
            assert "Firma Alpha" in content
            assert "1250.0" in content
            assert "3400.0" in content

    def test_empty_directory_handling(self, output_dir):
        """Empty input directory should produce header-only CSV."""
        import importlib.util
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            code_path = Path(tmp) / "agent_code.py"
            code_path.write_text(INVOICE_CODE)

            empty_dir = Path(tmp) / "empty"
            empty_dir.mkdir()

            spec = importlib.util.spec_from_file_location("agent_code", code_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            agent = mod.InvoiceScanner()
            inp = type('Input', (), {
                'task': 'scan',
                'context': {'input_dir': str(empty_dir), 'output_dir': str(output_dir)},
            })()

            result = agent.execute(inp)
            assert result.success
            assert result.result == []


# =========================================================================
# Phase 4: Rollback
# =========================================================================


class TestRollback:

    def test_agent_removed_on_rollback(self, app):
        """Rolling back should remove the invoice-scanner agent."""
        gen_before = app.config.apply_from_state(app.state_manager, "before", "test")

        spec = AgentSpec(name="invoice-scanner", description="Scan invoices",
                         capabilities_needed=["filesystem.read"])
        app._llm = _mock_llm(INVOICE_GHERKIN, INVOICE_TESTS, INVOICE_CODE)
        app._auditor = _approve_audit()

        CreatorPipeline(app).run(spec)
        assert app.agent_registry.get("invoice-scanner") is not None

        app.config.rollback(to_generation=gen_before, state_manager=app.state_manager)
        assert app.agent_registry.get("invoice-scanner") is None

    def test_code_survives_rollback_in_object_store(self, app):
        """Object Store files are immutable — code survives rollback."""
        gen_before = app.config.apply_from_state(app.state_manager, "before", "test")

        spec = AgentSpec(name="invoice-scanner", description="Scan invoices",
                         capabilities_needed=["filesystem.read"])
        app._llm = _mock_llm(INVOICE_GHERKIN, INVOICE_TESTS, INVOICE_CODE)
        app._auditor = _approve_audit()

        result = CreatorPipeline(app).run(spec)
        code_hash = app.agent_registry.get("invoice-scanner")["code_hash"]

        app.config.rollback(to_generation=gen_before, state_manager=app.state_manager)

        # Agent gone from registry, but code still in Object Store
        from mycelos.storage.object_store import ObjectStore
        store = ObjectStore(app.data_dir)
        assert store.exists(code_hash)
        assert "InvoiceScanner" in store.load(code_hash)


# =========================================================================
# Phase 5: Agent in NixOS Snapshot
# =========================================================================


class TestSnapshot:

    def test_agent_in_snapshot(self, app):
        spec = AgentSpec(name="invoice-scanner", description="Scan invoices",
                         capabilities_needed=["filesystem.read"])
        app._llm = _mock_llm(INVOICE_GHERKIN, INVOICE_TESTS, INVOICE_CODE)
        app._auditor = _approve_audit()

        CreatorPipeline(app).run(spec)

        snapshot = app.state_manager.snapshot()
        assert "invoice-scanner" in snapshot["agents"]
        assert snapshot["agents"]["invoice-scanner"]["code_hash"] is not None
        assert "filesystem.read" in snapshot["agents"]["invoice-scanner"]["capabilities"]
