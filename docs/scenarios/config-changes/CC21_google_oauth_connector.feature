@config-change @connector @google @oauth @blueprint-lifecycle @risk-high
Feature: Google OAuth Connector Setup (Gmail, Calendar, Drive)
  As a Mycelos user
  I want to connect my Google account via OAuth
  So that agents can access Gmail, Google Calendar, and Google Drive

  Google OAuth is the most common connector setup because many users
  have Gmail. This scenario covers the full OAuth flow including
  token refresh, scope management, and revocation.

  Background:
    Given the system is initialized
    And no Google connector is currently configured

  @google-oauth @gmail @happy-path
  Scenario: Gmail connector via Google OAuth
    When the user runs "mycelos connector setup google-mail"
    Then the system explains what will happen:
      """
      Google Mail Connector einrichten

      Ich oeffne eine Google-Anmeldeseite. Dort erlaubst du Mycelos:
      - Deine Emails zu lesen (Gmail API, read-only)
      - Emails in deinem Namen zu senden (optional, separat bestaetigbar)

      Dein Passwort sehe ich nie. Du kannst den Zugang jederzeit
      unter https://myaccount.google.com/permissions widerrufen.
      """
    When the user confirms
    Then the system starts a local HTTP server on localhost for the OAuth callback
    And opens the browser to Google's OAuth consent screen
    And requests the following scopes:
      | google_scope                              | mycelos_capability |
      | https://www.googleapis.com/auth/gmail.readonly | email.read   |
      | https://www.googleapis.com/auth/gmail.send     | email.send   |
    When the user grants access in the browser
    Then the OAuth callback receives the authorization code
    And the system exchanges the code for access + refresh tokens
    And both tokens are encrypted via the Credential Proxy
    And the tokens are NEVER stored in plaintext or .env files
    And the MCP email connector is registered with Gmail-specific config
    And a Blueprint Lifecycle runs:
      | phase   | result                                       |
      | resolve | ChangeSpec with gmail connector + capabilities|
      | verify  | SHA-256 hash, no duplicate                   |
      | plan    | Risk: HIGH (new external service + capabilities)|
      | apply   | New config generation created                 |
      | status  | 10 min guard period started                   |

  @google-oauth @token-refresh
  Scenario: Automatic token refresh when access token expires
    Given the Google Mail connector is configured
    And the access token has expired (default: 1 hour)
    When an agent requests email.read via the Credential Proxy
    Then the Credential Proxy detects the expired access token
    And uses the refresh token to obtain a new access token
    And the new access token is encrypted and stored
    And the agent's request proceeds without interruption
    And the user is NOT notified (transparent refresh)

  @google-oauth @refresh-token-revoked
  Scenario: User revokes access in Google Account settings
    Given the Google Mail connector is configured
    When the user revokes Mycelos's access at myaccount.google.com
    And an agent tries to use the email connector
    Then the Credential Proxy gets a 401 from Google
    And the token refresh also fails (refresh token revoked)
    And the agent's task fails with a clear error
    And the user is notified:
      """
      Dein Google-Zugang wurde widerrufen. Der Email-Agent kann
      nicht mehr auf Gmail zugreifen.

      Moechtest du den Zugang neu einrichten?
        mycelos connector setup google-mail
      """
    And the email-related scheduled tasks are paused (not deleted)
    And an audit event is logged: "credential.external_revocation"

  @google-oauth @calendar
  Scenario: Adding Google Calendar to an existing Google connection
    Given the Google Mail connector is already configured
    When the user runs "mycelos connector setup google-calendar"
    Then the system detects an existing Google OAuth connection
    And asks for additional scope:
      """
      Du hast bereits Google Mail verbunden. Fuer den Kalender
      brauche ich eine zusaetzliche Berechtigung.

      Ich oeffne die Google-Anmeldeseite nochmal — dort siehst du
      die neue Berechtigung "Kalender-Ereignisse lesen".
      """
    And requests incremental scope:
      | new_scope                                        | mycelos_capability  |
      | https://www.googleapis.com/auth/calendar.readonly | calendar.read     |
    When the user grants the additional scope
    Then the existing refresh token is updated with the new scope
    And the calendar MCP connector is registered
    And a Blueprint Lifecycle runs (Risk: HIGH, new capabilities)

  @google-oauth @security
  Scenario: OAuth state parameter prevents CSRF attacks
    When the OAuth flow starts
    Then the system generates a random state parameter
    And stores it in a short-lived session (5 minutes TTL)
    When the OAuth callback arrives
    Then the state parameter is verified against the stored value
    And if the state doesn't match, the callback is rejected
    And an audit event is logged: "security.oauth_csrf_attempt"

  @google-oauth @minimal-scopes
  Scenario: Request only the scopes actually needed
    When the Creator-Agent builds an email-summary agent
    And the agent only needs email.read (no email.send)
    Then the connector setup only requests gmail.readonly scope
    And email.send is NOT requested until actually needed
    And the system follows the principle of least privilege
    When the user later wants the agent to send emails
    Then the system requests the additional gmail.send scope via incremental auth
