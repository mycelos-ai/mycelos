@use-case @invoice @artifact @media-processing @milestone-5
Feature: Invoice Processing Pipeline with OCR and Data Extraction
  As a freelancer using Mycelos
  I want to automatically process incoming invoices
  So that I can track expenses without manual data entry

  Background:
    Given Mycelos is initialized and running
    And the filesystem connector is configured with read access to "~/invoices"
    And the email connector is configured

  @multi-agent @workflow
  Scenario: Multi-step invoice processing workflow
    Given the Creator-Agent has built three agents:
      | agent_name        | agent_type      | model_tier | purpose                    |
      | ocr-agent         | deterministic   | none       | PDF to text via Tesseract  |
      | extraction-agent  | light_model     | haiku      | Extract invoice fields     |
      | booking-agent     | light_model     | haiku      | Categorize and log expense |
    When the user says "Process all new PDFs in ~/invoices"
    Then the Planner creates a workflow with chained steps:
      | step_id        | agent              | policy  | inputs                |
      | ocr            | ocr-agent          | always  | PDF from /input/      |
      | extract        | extraction-agent   | always  | text from ocr step    |
      | book           | booking-agent      | confirm | extracted fields      |

  @artifact @derivation-chain
  Scenario: Artifact derivation chain is created
    Given a new invoice "invoice-2026-03.pdf" is uploaded
    When the workflow runs the OCR step
    Then artifact "invoice-2026-03.txt" is created with:
      | property         | value                    |
      | derived_from     | invoice-2026-03.pdf      |
      | derivation_type  | ocr                      |
      | processing_status| enriched                 |
    When the extraction step runs
    Then artifact "invoice-2026-03.json" is created with:
      | property         | value                    |
      | derived_from     | invoice-2026-03.txt      |
      | derivation_type  | extraction               |
    And the JSON contains fields: amount, date, sender, vat_id, line_items
    And each artifact has a unique content_hash (SHA-256)

  @sandbox @privacy
  Scenario: Each agent only sees its assigned artifacts
    When the ocr-agent runs
    Then its sandbox contains:
      | path        | access     | content                 |
      | /input/     | read-only  | invoice-2026-03.pdf     |
      | /workspace/ | read-write | temporary working dir   |
      | /output/    | write-only | produced text file       |
    And the agent cannot see other tasks' artifacts
    And the agent cannot access ~/invoices directly (only through connector)

  @event-trigger @filesystem
  Scenario: New invoices trigger processing automatically
    Given a filesystem event trigger watches "~/invoices/*.pdf"
    And the gateway is running
    When a new file "invoice-april.pdf" appears in ~/invoices
    Then the filesystem watcher detects the new file
    And a task is enqueued via Huey with the invoice workflow
    And the permission set from workflow creation is used
    And no new permissions are requested at runtime

  @cost-optimization
  Scenario: Deterministic OCR agent keeps costs near zero
    Given 50 invoices have been processed
    Then the ocr-agent has cost $0.00 (deterministic, no LLM)
    And the extraction-agent has used Haiku (low cost)
    And the booking-agent has used Haiku (low cost)
    And the total cost is tracked in model_usage per task
