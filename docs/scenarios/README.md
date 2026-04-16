# Mycelos Scenarios

Gherkin-based scenario specifications for Mycelos, organized into three categories. These serve as both documentation and future automated test specs.

## Structure

```
docs/scenarios/
  use-cases/          # 20 end-to-end user journeys
  config-changes/     # 20 configuration change scenarios
  security/           # 20 security threat analysis scenarios
```

## Quick Stats

- **60 total .feature files** across 3 categories
- **Tags** for filtering: @security, @milestone-1..5, @risk-high, @prompt-injection, etc.
- **Gherkin format** compatible with pytest-bdd, behave, or cucumber

## Running as Tests

```bash
# Install test framework
pip install pytest-bdd

# Run all scenarios
pytest docs/scenarios/

# Run by category
pytest docs/scenarios/use-cases/
pytest docs/scenarios/config-changes/
pytest docs/scenarios/security/

# Run by tag
pytest -m "security"
pytest -m "milestone-1"
pytest -m "prompt-injection"
```

## Categories

### [Use Cases](use-cases/README.md)
20 realistic user journeys from onboarding to advanced multi-agent workflows.

### [Config Changes](config-changes/README.md)
20 configuration change scenarios with Blueprint Lifecycle phases and risk classification.

### [Security Threats](security/README.md)
20 attack vector analyses with threat modeling and defense-in-depth verification.
