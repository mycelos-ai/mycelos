# Mycelos Architecture & Security Review

**Date:** 2026-03-20  
**Scope:** Architecture, security concepts, system design, operational maturity  
**Out of scope:** Frontend quality and unfinished connector implementations

---

## Executive summary

Mycelos is **not** a throwaway agent prototype. The codebase already shows a recognisable platform architecture with explicit boundaries for storage, security, orchestration, configuration, audit, and execution. The strongest parts of the system are:

- the **protocol-driven modular design**,
- the **security layer separation**,
- the **configuration generation model with rollback**, and
- the fact that **security scenarios are backed by concrete tests**.

At the same time, the project is still in an **early-to-mid maturity phase**. The core idea is strong, but runtime hardening still lags behind the conceptual design. In particular, the current `LocalSandbox` is explicitly documented as **not** being a real security boundary, which means the project should currently be classified as **security-aware and structurally strong, but not yet production-hardened for untrusted execution**.

### Overall rating

| Area | Rating | Comment |
|---|---|---|
| Architecture | **8/10** | Clear subsystem decomposition, strong foundation |
| Security concept | **7.5/10** | Thoughtful model, but runtime isolation is not finished |
| Operational maturity | **6/10** | Good internal structure, still some packaging/runtime gaps |
| Extensibility | **8/10** | Interfaces and registries make future replacement plausible |
| Production readiness | **5.5/10** | Strong prototype / pre-production foundation |

### Classification

> **Mycelos is a serious, security-conscious early platform implementation with above-average architectural quality, but it is not yet a hardened production agent operating system.**

---

## What the project does well

### 1. Clear architectural decomposition

A major strength of the repository is that the important subsystems are separated conceptually and structurally:

- storage,
- memory,
- LLM access,
- sandboxing,
- audit logging,
- credential management,
- policy evaluation,
- capability token handling,
- and the agent runtime contract.

This is visible in `protocols.py`, where the core services are described as Protocol-based contracts rather than being hidden behind one monolithic service object. That gives the system a good basis for future replacement, mocking, testing, and hardening. The code explicitly frames the architecture as “Protocol + Implementation”, which is exactly the right instinct for a system that wants to evolve from local prototype to platform runtime.

**Why this matters:** many agent projects fail because orchestration, tool execution, storage, and trust boundaries are mixed together too early. Mycelos largely avoids that mistake.

---

### 2. Security is treated as a first-class system concern

The codebase does not treat security as a prompt trick or a post-processing afterthought. Instead, there are dedicated modules for:

- encrypted credential storage,
- capability token issuance and validation,
- policy decisions,
- outbound sanitization,
- inbound file checks,
- and the execution security pipeline.

The execution runtime explicitly models a pipeline of:

1. request parsing,
2. auth validation,
3. capability validation,
4. policy check,
5. tool execution,
6. output sanitization.

That is the right overall direction. Even more importantly, the system contains tests for security invariants rather than relying on architectural claims alone.

**Why this matters:** the project is building the right trust model, even if the implementation is not fully hardened yet.

---

### 3. Configuration generations are unusually strong for this maturity level

The `ConfigGenerationManager` is one of the best architectural components in the repo.

Its design introduces:

- immutable config generations,
- content-addressed storage via hash reuse,
- atomic active-generation switching,
- rollback support,
- semantic diffs between generations.

This is very valuable for agent systems because policies, model providers, tool availability, and workflow definitions often change rapidly and can cause subtle security or behaviour regressions.

**Why this matters:** this subsystem gives the project a path to safe change management, auditability, and operational reversibility.

---

### 4. The persistence model reflects a real operating system mindset

The schema is not just chat logs and tasks. It models:

- tasks,
- agents,
- plans,
- attempts,
- config generations,
- memory entries,
- audit events,
- workflow events,
- credentials,
- capability tokens,
- policies.

That is a good sign. It means the project is thinking in terms of a long-lived control plane rather than just an interactive AI shell.

**Why this matters:** durable execution, rollback, audit, and governance become possible only if they are baked into the data model early.

---

### 5. Honest treatment of current security limitations

The code and tests explicitly document that `LocalSandbox` is **not** a true security boundary and that stronger isolation is still planned. This honesty is a strength.

Many systems accidentally drift into production while developers still subconsciously treat a dev sandbox as secure. Here, the repo clearly states that the current sandbox is development/test-oriented and that real containment still needs a stronger backend.

**Why this matters:** explicit limitations are far safer than implicit false confidence.

---

## Main architectural and security concerns

### 1. Runtime isolation is currently the biggest gap

This is the most important issue in the project.

The sandbox strips environment variables and constrains working directories, which is useful, but it is still just a subprocess wrapper around temporary folders. There is currently no strong indication of:

- kernel-level isolation,
- namespace isolation,
- seccomp/AppArmor restrictions,
- strong filesystem confinement,
- cgroup-level resource control,
- or actual network denial.

The tests even document that network isolation is not currently enforced.

**Impact:** if the system is meant to run untrusted or partially trusted agent code, the current sandbox is insufficient as a production boundary.

**Assessment:** this is not a design failure, but it is the primary blocker between “well-designed prototype” and “secure production runtime”.

---

### 2. Multi-user and tenant semantics are not fully carried through yet

The repo includes `user_id` on several entities, which is a good sign. However, a lot of flows still default to a single-user assumption, especially around the use of `"default"` as a user context.

That is acceptable for early local-first development, but it becomes dangerous once the system supports:

- multiple users,
- multiple operator identities,
- multiple workspaces,
- or long-lived policy scopes.

One especially important design smell is that policy checks in the execution pipeline still appear to use a hardcoded default user context rather than propagating principal identity from the actual request or token.

**Impact:** over time this can create confusing or unsafe authorization behaviour, especially if tokens, sessions, policies, and tasks are not all bound to the same principal model.

---

### 3. The `App` container is useful now, but could become a bottleneck later

The central `App` object is currently acting as the composition root and as a lazy service locator for most of the runtime.

That is fine for this stage, but there is a medium-term risk that it becomes:

- too global,
- too stateful,
- too implicit,
- and too hard to reason about under concurrency or different runtime scopes.

This is a common evolution path in early platforms: what starts as a tidy service container later turns into a hidden dependency graph.

**Impact:** as the system grows, test isolation, scoped runtime construction, and multi-process execution may become harder than necessary.

---

### 4. Security controls are good, but still relatively lightweight in depth

The system has the right pieces, but some parts are still lightweight implementations of bigger ideas:

- credential protection is strong at-rest encryption, but master-key lifecycle and derivation strategy may need stronger operational treatment later,
- the sanitizer is useful, but regex-based redaction is never a complete DLP boundary,
- the policy model is intentionally simple and readable, but not yet context-rich,
- capability tokens are appropriate for this phase, but they are still essentially DB-backed bearer capabilities.

**Impact:** the system has a strong **security architecture direction**, but some controls are still at “solid MVP” depth rather than “high assurance” depth.

---

### 5. Orchestration is still intentionally thin

The current chat orchestrator is elegant and easy to understand, but it is also still narrow in capability:

- classify via LLM,
- route to task flow, creation flow, system command, or conversation,
- fall back safely on parsing/classification failure.

This is a good v1, but not yet a full decision engine.

As the system grows, it will likely need:

- richer context assembly,
- confidence thresholds,
- deterministic overrides,
- disambiguation rules,
- stronger session/user binding,
- and clearer system-command execution paths.

**Impact:** not a current problem, but likely a future refactor pressure point.

---

## Detailed ratings by category

### Architecture — **8/10**

**Strengths**
- Good subsystem boundaries
- Interfaces are explicit
- Composition is understandable
- Config generation design is excellent
- Data model supports long-lived runtime concerns

**Weaknesses**
- `App` risks becoming a global service hub
- Some modules still rely on implicit shared context
- Orchestrator is structurally clean but still thin

**Verdict**
A very good early architecture with enough clarity to scale, provided the next phase includes selective refactoring instead of just adding features.

---

### Security concept — **7.5/10**

**Strengths**
- Credential proxy separation
- Capability token model
- Policy engine
- Sanitization layer
- Security-specific test coverage
- Clear awareness of trust boundaries

**Weaknesses**
- Sandbox is not yet a real containment boundary
- Some authorization context is still simplified
- Sanitization is pattern-based rather than policy/data-model-based

**Verdict**
Much stronger than average for an agent project, but still not hardened enough to claim secure untrusted execution.

---

### Operational maturity — **6/10**

**Strengths**
- SQLite WAL is a pragmatic local-first storage choice
- The schema supports auditability and rollback
- The system is structured enough to be operable

**Weaknesses**
- Test execution in the current repo/environment is not frictionless
- Some dependencies and environment assumptions are still manual
- Packaging/runtime ergonomics still need tightening

**Verdict**
A strong engineering base, but still in the zone where contributors need internal knowledge to operate the project comfortably.

---

### Extensibility — **8/10**

**Strengths**
- Protocols make replacement feasible
- Registries and separable services suggest future composability
- Connector and runtime growth seem anticipated by the design

**Weaknesses**
- Real extensibility will depend on how far request context and policy context are carried across boundaries
- Composition may become harder if the container grows too implicit

**Verdict**
The project is structurally well positioned to grow into a larger system.

---

### Production readiness — **5.5/10**

**Why not higher?**
- Missing hardened isolation boundary
- Some single-user assumptions still leak through
- Operational packaging/testing path still rough

**Why not lower?**
- Core concepts are much better than typical prototypes
- Security and governance are already part of the design
- The data and control plane are not accidental

**Verdict**
This is best described as a **pre-production foundation with serious intent**, not yet a production-hardened agent OS.

---

## Prioritized recommendations

### Priority 1 — Finish real execution isolation

This should be the next major systems milestone.

Recommended direction:

- container or namespace-based sandbox backend,
- explicit network policy,
- read-only root where possible,
- mount-scoped input/work/output paths,
- resource limits,
- and a clearly documented production execution mode.

**Expected outcome:** closes the biggest trust gap in the system.

---

### Priority 2 — Introduce a consistent principal model

The system should converge on a single identity model that flows consistently through:

- sessions,
- tasks,
- tokens,
- policies,
- memory,
- audit,
- and execution runtime.

Recommended additions:

- explicit `Principal`/`ActorContext` object,
- propagation from session or token into execution and policy checks,
- removal of hardcoded default-user assumptions from security-sensitive paths.

**Expected outcome:** prevents future authorization confusion and makes multi-user support real rather than superficial.

---

### Priority 3 — Deepen the policy model

Current decisions are easy to understand, which is good. The next step is to make them expressive enough for real operations.

Recommended additions:

- conditions by resource argument,
- read vs write vs side-effect classes,
- policy provenance and review metadata,
- generation-bound policies,
- approval records,
- partial delegation/escrow-like execution modes.

**Expected outcome:** turns a readable policy layer into a genuinely powerful governance system.

---

### Priority 4 — Reduce reliance on global lazy service access

Keep the `App` composition root, but gradually shift hot execution flows toward explicit dependency bundles or scoped runtime objects.

Good candidates:

- request handling,
- task execution,
- workflow execution,
- connector/runtime invocation contexts.

**Expected outcome:** easier testing, cleaner concurrency, fewer hidden dependencies.

---

### Priority 5 — Tighten developer and CI ergonomics

The repository should make the standard verification path extremely obvious and reliable.

Recommended improvements:

- ensure tests run via the documented default path,
- make editable/dev installs the default contributor setup,
- verify all required test dependencies are pulled in consistently,
- add a standard CI command path for architecture/security checks.

**Expected outcome:** improves contributor confidence and reduces accidental environment drift.

---

## Final assessment

If I had to describe Mycelos in one line, I would say:

> **Mycelos already has the bones of a credible local-first agent platform, with unusually strong architecture and security intent for its stage, but it still needs runtime hardening and identity/policy refinement before it can be considered production-grade.**

That is a very positive assessment overall.

The project is already beyond “interesting prototype” territory. The main question is no longer whether the architecture is serious enough — it is. The next question is whether the implementation phase can now preserve that clarity while hardening the execution model and reducing the remaining implicit assumptions.

If that happens, the project could become a genuinely strong foundation for a secure agent operating system.
