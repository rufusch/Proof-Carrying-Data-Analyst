# Answer review and recovery

The existing contract is extended with optional `AnalysisResult.review` and `AnalysisResult.recovery` fields and a `POST /analyses/{analysis_id}/recovery` operation. Original fields and routes remain available. VerificationCheck supports two additional check types: `units_filters` and `skeptic`. The original frontend-backend contract document is retained; `openapi.yaml` describes the extensions.

## Two independent analysts

Agent 1 executes allowlisted DuckDB SQL. Agent 2 recomputes from typed source rows using Python loops and Decimal arithmetic, without SQL or Pandas aggregation. Every output field in every group is compared at relative tolerance `1e-10`, absolute tolerance `1e-9`. Approved SQL, the output hash, identical unit/filter/rate semantics, and a fresh isolated rerun also gate publication. No model-generated numerical answer is trusted: proposed claims must be the actual executed outputs. A disagreement publishes no answer metrics or evidence claims.

## Skeptic reviewer

This is a bounded deterministic adversarial reviewer, not an independently trained LLM. It reverses source order, removes exact duplicate rows, tries deduplication by one identifier per table when repeated identifiers occur, reads naive midnight timestamps as equivalent ISO dates, and tests at most one alternative shared identifier join that meets uniqueness and full-coverage rules. Ambiguous date order, unsafe joins, and incomplete period inputs are rejected before these tests. Material changes fail the skeptic gate and withhold the answer. A corrected dataset is required; the reviewer cannot silently choose a deduplication policy or weaken the original plan. Passing does not prove robustness to every possible interpretation.

## Counterfactual sensitivity

Every reported numerical output, including each grouped cell and group count, has scenario-specific changes and relative changes. Zero-baseline percentages are undefined. Exact duplicate and identifier-dedupe impacts are measured recalculations. Missing numeric cells are also evaluated using observed minimum/maximum values; these are explicitly assumed scenarios, **not** established bounds or calibrated uncertainty. Without supplied bounds, missing-value/rate impacts cannot be reliably bounded or ranked against measured issues. No invented ± percentage is reported. Candidate values in refused-review diagnostics are never released as final answer claims.

## Parameterized refusals

For `total revenue for March in USD`, a source field explicitly marked EUR produces a refusal with the required EUR→USD input, a standalone Decimal function template, and a recovery form. The user supplies a positive finite rate up to 1,000,000 and its provenance. Recovery creates a new analysis linked to the refusal; it preserves the original filters, records the supplied inputs, and passes the complete review pipeline. Client code is not accepted or executed. Conversion currently supports one optionally filtered/grouped column total without joins or period-comparison operations. The supplied rate is explicitly applied uniformly: different rate periods require a narrower question or pre-converted data. Symbols alone do not establish currency codes. Other refusals state concrete remediation; unsupported forecasts do not acquire fake executable templates.

The release script includes these extensions in the GitHub-ready source ZIP and excludes credentials, uploaded data, databases, and runtime logs. Packaging never pushes to GitHub.


## Current interface

The evidence heatmap has been removed. Its legacy API field remains an empty array for compatibility. Agent 1 and Agent 2 independently verify the calculation. AuditCode replaces the calculation explanation with a copyable standalone Python script containing the actual query and normalized source records. Install DuckDB and pandas and run the script to reproduce the calculation. Copying this script also copies the source records it contains.
