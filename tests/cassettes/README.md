# LLM Cassettes

These files are recorded LLM responses replayed by integration tests, so the
suite can run without hitting the Anthropic API on every CI build.

## How it works

`tests/integration/conftest.py::integration_app` installs a `CassetteRecorder`
on the test's LLM broker. Each LLM call is fingerprinted (sha256 of model +
messages + tools) and looked up in the test's cassette file. On hit, the
recorded response is returned without an API call. On miss, behavior depends
on the mode.

## Modes

Set with the `MYCELOS_LLM_CASSETTE` environment variable.

| Mode    | Behavior on hit | Behavior on miss                |
| ------- | --------------- | ------------------------------- |
| replay  | replay          | raise `CassetteMissError`       |
| record  | call real API   | call real API + write cassette  |
| auto    | replay          | call real API + write cassette  |

**Default: replay.** This is what CI uses. Local development without an API
key also defaults to replay.

## Re-recording

When a test prompt or expected behavior changes, the cassette becomes stale
and the test will fail with a mismatch. To re-record:

```bash
# 1. Delete the stale cassette
rm tests/cassettes/test_creator_agent__test_creates_simple_agent.json

# 2. Re-run the test in auto mode with a real API key
ANTHROPIC_API_KEY=sk-ant-... MYCELOS_LLM_CASSETTE=auto \
  python -m pytest tests/integration/test_creator_agent.py::test_creates_simple_agent -v

# 3. Commit the new cassette
git add tests/cassettes/test_creator_agent__test_creates_simple_agent.json
git commit -m "test(cassette): re-record after prompt change"
```

## Why these are checked into git

- CI can run integration tests without secrets.
- Reviewers can see what the LLM actually returned for a given test in the diff.
- A bad re-record (e.g. picked up a transient API hiccup) is visible and revertable.

## Why not VCR / vcrpy / record at HTTP level?

The cassette layer sits at `LLMBroker.complete`, which is provider-agnostic.
We don't care whether the underlying call went to Anthropic, OpenAI, Ollama,
or our SecurityProxy — they all return our normalized `LLMResponse`. An
HTTP-level recorder would have to know each provider's response shape and
break on every protocol change.

## Known limitation: streaming

The recorder intercepts at `LLMBroker.complete`. For calls made with
`stream=True`, the recorder is NOT bypassed — it will still fingerprint the
request and either replay or call the real API. However, the recorded
response is the final `LLMResponse`, not the token-by-token stream. Tests
that depend on the streaming shape must remain in `record` mode or skip the
recorder.
