# MMI H2c Prospective Cohort Log Template

## Status and authority boundary

This is an operator-owned template for the planned primary prospective H2C
paired-comparison cohort. Maintain the actual log outside frozen case roots.
It is not consumed by deterministic code.

Every cohort and case entry is:

```text
operator-authored
unauthenticated
observational
non-authoritative
not consumed by deterministic code
not a gate
not a permission source
not execution authority
```

Do not record credentials, cookies, session tokens, provider conversation
identifiers, response contents, or private authentication material.

## Cohort header

```text
cohort_protocol_version = prospective_h2c_manual_llm_cohort_v1
cohort_lock_utc = <UTC timestamp before the first response-generating submission>
planned_case_count = <5-10>

h1_surface = ChatGPT ordinary chat
h1_displayed_model = GPT-5.6 Sol
h1_displayed_reasoning = Extra High
h1_deep_research = OFF
h1_web_search = OFF
h1_external_context = NONE

legacy_surface = ChatGPT Deep Research
legacy_displayed_model = GPT-5.6 Sol
legacy_displayed_reasoning = Extra High
legacy_deep_research_variant = full/default
legacy_public_web = ON
legacy_external_context = NONE

cross_side_isolation_policy = separate fresh conversations/tasks; no cross-side or cross-case response exposure; no comparison commentary; no prompt changes after observing another response
one_submission_policy = exactly one response-generating submission per side per prepared case; no retry, regeneration, branch, edit-and-resubmit, follow-up for a better answer, repair, or selection among responses
substitution_policy = STOP on a frozen material visible configuration change; no automatic substitution; approved material change starts a new cohort version
```

Matching displayed values are observational records only. They do not
authenticate the provider, hidden model/backend, reasoning execution, Deep
Research backend, or tool trajectory.

## Per-case entry

Create one entry for every attempted case, including incomplete and
out-of-protocol cases.

```text
case_label = <operator case label>
case_root = <absolute case root>
prepared_case_identity_sha256 = <lowercase sha256>
h1_prompt_sha256 = <lowercase sha256>
legacy_prompt_sha256 = <lowercase sha256>

h1_submission_started_utc = <UTC timestamp or NOT_SUBMITTED>
h1_response_received_utc = <UTC timestamp or NOT_RECEIVED>

legacy_submission_started_utc = <UTC timestamp or NOT_SUBMITTED>
legacy_response_received_utc = <UTC timestamp or NOT_RECEIVED>

observed_h1_surface = ChatGPT ordinary chat
observed_h1_displayed_model = GPT-5.6 Sol
observed_h1_displayed_reasoning = Extra High

observed_legacy_surface = ChatGPT Deep Research
observed_legacy_displayed_model = GPT-5.6 Sol
observed_legacy_displayed_reasoning = Extra High

protocol_deviation = NONE | <concise material deviation>
case_protocol_status = IN_PROTOCOL | OUT_OF_PROTOCOL | INCOMPLETE
```

`IN_PROTOCOL`, `OUT_OF_PROTOCOL`, and `INCOMPLETE` are the only allowed
`case_protocol_status` values. A response-generating submission with a wrong
material configuration is `OUT_OF_PROTOCOL`; retain that case and do not count
it in the primary cohort. A pre-submission configuration correction does not by
itself consume a prepared case.
