@slash-commands @chat
Feature: UC35 — Slash Commands
  As a Mycelos user
  I want to use slash commands for system operations
  So that sensitive actions bypass the LLM and execute securely

  Background:
    Given a freshly initialized Mycelos instance

  # --- /help ---

  Scenario: /help shows all available commands
    When the user types "/help"
    Then the response lists "/memory"
    And the response lists "/cost"
    And the response lists "/mount"
    And the response lists "/config"
    And the response lists "/agent"
    And the response lists "/connector"
    And the response lists "/schedule"
    And the response lists "/workflow"
    And the response lists "/model"
    And the response lists "/sessions"

  # --- /memory ---

  Scenario: /memory list on empty memory
    When the user types "/memory list"
    Then the response says memory is empty

  Scenario: /memory list with entries
    Given memory contains entry "user.preference.theme" = "dark"
    When the user types "/memory list"
    Then the response includes "Preferences"
    And the response includes "theme"

  Scenario: /memory search finds match
    Given memory contains entry "user.fact.language" = "Python"
    When the user types "/memory search Python"
    Then the response shows 1 result

  Scenario: /memory search no match
    When the user types "/memory search nonexistent"
    Then the response says no entries found

  Scenario: /memory delete removes entry
    Given memory contains entry "user.fact.temp" = "temporary"
    When the user types "/memory delete user.fact.temp"
    Then the response confirms deletion

  Scenario: /memory clear preserves user name
    Given the user name "Stefan" is stored in memory
    And memory contains entry "user.preference.theme" = "dark"
    When the user types "/memory clear"
    Then the user name "Stefan" is still in memory
    And the preference is cleared

  # --- /cost ---

  Scenario: /cost with no usage
    When the user types "/cost"
    Then the response says no usage recorded

  Scenario: /cost today shows totals
    Given LLM usage records exist for today
    When the user types "/cost today"
    Then the response shows dollar amounts
    And the response shows token counts
    And the response shows model breakdown

  Scenario: /cost all shows all-time usage
    Given LLM usage records exist from last month
    When the user types "/cost all"
    Then the response includes all recorded usage

  Scenario: /cost by purpose
    Given LLM usage for purpose "chat" exists
    And LLM usage for purpose "classification" exists
    When the user types "/cost all"
    Then the response shows "chat"
    And the response shows "classification"

  Scenario: /cost invalid period
    When the user types "/cost invalid"
    Then the response shows usage instructions

  # --- /mount ---

  Scenario: /mount list when empty
    When the user types "/mount list"
    Then the response says no directories mounted

  Scenario: /mount add read-only
    Given directory "/tmp/test-docs" exists
    When the user types "/mount add /tmp/test-docs --read"
    Then a mount is created with read access
    And a new config generation is created

  Scenario: /mount add write access
    Given directory "/tmp/test-output" exists
    When the user types "/mount add /tmp/test-output --write"
    Then a mount is created with write access

  Scenario: /mount add for specific agent
    Given agent "news-agent" exists
    And directory "/tmp/test-news" exists
    When the user types "/mount add /tmp/test-news --read --agent news-agent"
    Then a mount is created scoped to "news-agent"

  Scenario: /mount revoke
    Given a mount exists for "/tmp/test-docs"
    When the user types "/mount revoke <mount-id>"
    Then the mount status becomes "revoked"
    And a new config generation is created

  Scenario: /mount add nonexistent path
    When the user types "/mount add /nonexistent/xyz --read"
    Then the response says path does not exist

  # --- /agent ---

  Scenario: /agent list when empty
    When the user types "/agent list"
    Then the response says no agents registered

  Scenario: /agent list shows agents
    Given agent "news-agent" exists with capability "search.web"
    When the user types "/agent list"
    Then the response includes "news-agent"
    And the response includes "search.web"

  Scenario: /agent info shows details
    Given agent "news-agent" exists with capability "search.web"
    When the user types "/agent news-agent info"
    Then the response includes agent name
    And the response includes capabilities
    And the response includes reputation

  Scenario: /agent grant adds capability
    Given agent "news-agent" exists
    When the user types "/agent news-agent grant filesystem.read"
    Then agent "news-agent" has capability "filesystem.read"
    And a new config generation is created

  Scenario: /agent revoke removes capability
    Given agent "news-agent" exists with capability "search.web"
    When the user types "/agent news-agent revoke search.web"
    Then agent "news-agent" does not have capability "search.web"
    And a new config generation is created

  Scenario: /agent for nonexistent agent
    When the user types "/agent ghost info"
    Then the response says agent not found

  # --- /connector ---

  Scenario: /connector list shows recipes
    When the user types "/connector list"
    Then the response lists available connector recipes

  Scenario: /connector add no-key connector
    When the user types "/connector add filesystem"
    Then the connector is activated without credentials
    And a new config generation is created

  Scenario: /connector add with credentials needed
    When the user types "/connector add github"
    Then the response explains credentials are needed
    And the response shows terminal setup command

  Scenario: /connector search
    When the user types "/connector search notion"
    Then the response lists matching MCP servers

  # --- /schedule ---

  Scenario: /schedule list when empty
    When the user types "/schedule list"
    Then the response says no scheduled tasks

  Scenario: /schedule list shows tasks
    Given a scheduled task exists for workflow "news-summary"
    When the user types "/schedule list"
    Then the response includes "news-summary"

  # --- /workflow ---

  Scenario: /workflow list when empty
    When the user types "/workflow list"
    Then the response says no workflows defined

  Scenario: /workflow runs when empty
    When the user types "/workflow runs"
    Then the response says no workflow runs

  # --- /model ---

  Scenario: /model list shows configured models
    When the user types "/model list"
    Then the response includes model information

  # --- /config ---

  Scenario: /config delegates to context handler
    When the user types "/config"
    Then the response includes configuration information

  # --- /sessions ---

  Scenario: /sessions with no sessions
    When the user types "/sessions"
    Then the response says no sessions found

  # --- Unknown command ---

  Scenario: Unknown slash command returns error
    When the user types "/foobar"
    Then the response says "Unknown command"
    And the response suggests "/help"

  # --- Confirmable Commands ---

  @confirmable
  Scenario: LLM response with mount command is detected
    When the LLM responds with text containing "`/mount add ~/Downloads --rw`"
    Then the system extracts 1 confirmable command
    And the extracted command is "/mount add ~/Downloads --rw"

  @confirmable
  Scenario: LLM response with agent grant is detected
    When the LLM responds with text containing "`/agent news-agent grant search.web`"
    Then the system extracts 1 confirmable command

  @confirmable
  Scenario: LLM response with no commands
    When the LLM responds with text containing no backtick commands
    Then the system extracts 0 confirmable commands

  @confirmable
  Scenario: Non-slash backtick content is ignored
    When the LLM responds with text containing "`pip install mycelos`"
    Then the system extracts 0 confirmable commands

  @confirmable
  Scenario: Multiple commands extracted
    When the LLM responds with text containing "`/mount add ~/a --read`" and "`/connector add github`"
    Then the system extracts 2 confirmable commands
