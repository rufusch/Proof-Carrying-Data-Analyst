# SureCount — Scope Note

SureCount helps financial data analysts ask questions about uploaded spreadsheets and inspect how an answer was calculated.

## MVP features

- Upload multiple CSV, XLS, or XLSX files and preview their tables.
- Ask everyday-language questions about totals, averages, counts, grouped results, differences, ratios, and growth between reporting periods.
- Match supported financial terms to dataset fields and ask for clarification when a question is ambiguous.
- Display results, units, source evidence, and a confidence score; download results or rerun a question.
- Expand AuditCode when needed, copy it, and reproduce the calculation using the included normalized records.
- Run parsing and calculations in an isolated Docker environment behind a contract-validated API.

## Novelty features

- **Ready-to-complete answers:** missing exchange-rate questions identify the required rate and provide a parameterized code template and input form. Other unsupported questions give concrete next steps.
- **Two independent checks:** Agent 1 calculates with DuckDB SQL; Agent 2 recomputes using Python Decimal arithmetic. Results, units, filters, and repeat execution must agree before an answer is released.
- **Extra reviewer:** a bounded skeptic tests source order, duplicate removal, eligible alternative identifiers, date representations, and eligible joins. Material disagreement withholds the answer.
- **What would change this?:** measured scenarios show how a data issue changes each reported number. Missing-value scenarios are labeled assumptions; unknown impacts are not assigned invented percentage bounds.

## Current limits

This MVP supports a defined set of spreadsheet calculations, rather than every possible financial question or forecasting task. Confidence is a heuristic, not a calibrated probability. Reviewer checks cover selected alternatives, not every interpretation. Currency recovery currently applies one supplied rate uniformly to an optionally filtered or grouped total. The evidence heatmap is not part of the current interface.
