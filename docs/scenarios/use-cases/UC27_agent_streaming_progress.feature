@use-case @streaming @progress @ipc @milestone-3
Feature: Agent Streaming and Progress Updates
  As a Mycelos user
  I want to see what my agents are doing in real-time
  So that I know the system is working and can estimate how long things take

  Agents can send progress updates while they work. These updates
  appear in the active channel (terminal spinner, Telegram status,
  inbox). Streaming is optional — agents that don't use it still work.

  Background:
    Given Mycelos is running and the user is in an active chat session
    And the email-summary agent is registered and active

  @progress @terminal
  Scenario: User sees progress updates in terminal
    When the email-summary agent starts processing 50 emails
    Then the terminal shows a progress indicator:
      """
      ⏳ Email-Agent: Verarbeite Email 1 von 50...
      ⏳ Email-Agent: Verarbeite Email 17 von 50...
      ⏳ Email-Agent: Zusammenfassung wird erstellt...
      ✅ Email-Agent: Fertig (3.2s)
      """
    And progress updates replace each other (no scroll spam)
    And the final result appears after the last progress update

  @progress @telegram
  Scenario: User sees progress updates on Telegram
    Given the user is connected via Telegram
    When the email-summary agent starts processing
    Then Telegram shows an updating message:
      """
      ⏳ Verarbeite deine Emails...
      """
    And the message is edited in-place as progress updates arrive
    And when done the message is replaced with the final result

  @progress @long-running
  Scenario: Long-running agent keeps user informed
    Given an invoice processing workflow with 3 steps
    When the workflow starts
    Then the user sees step-by-step progress:
      """
      [1/3] OCR: Lese Rechnung... (2s)
      [2/3] Extraktion: Erkenne Felder... (1s)
      [3/3] Validierung: Prüfe Ergebnisse...
      ✅ Rechnung verarbeitet (4.1s, $0.003)
      """

  @no-progress @silent
  Scenario: Agent without progress updates still works
    Given a deterministic agent that doesn't call progress()
    When the agent runs
    Then the terminal shows a generic spinner:
      """
      ⏳ Agent läuft...
      """
    And when done the result appears normally
    And no error occurs due to missing progress calls

  @streaming @llm
  Scenario: LLM response is streamed token by token
    When the user asks a question that requires an LLM response
    And the agent calls run(tool="llm.complete", args={..., "stream": True})
    Then the response appears word by word in the terminal
    And on Telegram the message is progressively edited
    And the user doesn't wait for the full response before seeing output

  @progress @ipc
  Scenario: Progress flows through IPC without security overhead
    Given an agent is running in a sandbox
    When the agent calls progress("Processing...")
    Then a JSON-RPC notification is sent: {"method": "progress", "params": {"text": "..."}}
    And the notification does NOT go through capability token validation
    And the notification does NOT go through the Security Layer
    And the notification is forwarded directly to the active channel
    And the notification is NOT stored in the conversation history (ephemeral)
