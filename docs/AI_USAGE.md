# AI Tool Usage

Claude Code (Anthropic's CLI coding agent, running the Claude model family) was used to plan, implement, test,
and document this entire submission in one working session, directly in this repository. Concretely:

- **Source analysis**: Claude Code extracted and read all six PDFs, the Excel workbook, and the assessment DOCX
  programmatically (via `pypdf`, `openpyxl`, `python-docx`) before writing any application code, and cross-checked
  every number that appears in `docs/SOURCE_ANALYSIS.md` against the actual extracted text rather than trusting
  the assessment prompt's paraphrase of the pack.
- **Backend implementation**: the FastAPI app, SQLite schema, ingestion/regex-extraction pipeline, BM25
  retrieval, access-controlled repositories, source-authority resolution, deterministic calculations, two-phase
  action-confirmation system, and the Anthropic tool-use agent loop were all written by Claude Code.
- **Frontend implementation**: the React + Vite + TypeScript + Tailwind chat UI, Issue Radar page, and Audit Log
  page were written by Claude Code, including fixing real build/lint issues it hit along the way (a broken
  `rolldown`/`oxlint` native-binding install on this Windows/Node 20.18 combination, resolved by pinning to Vite 5
  and standard ESLint instead of silently ignoring the failure).
- **Testing**: the 70-test pytest suite (ingestion, access control, calculations, conflicts, retrieval, actions,
  agent orchestration with a scripted fake LLM, and API-level tests) and `scripts/evaluate.py` were written by
  Claude Code and actually executed — every test result reported in `README.md` and in the final summary reflects
  a real `pytest` run in this environment, not a claimed/assumed pass.
- **Documentation**: this file and the other `docs/*.md` files were drafted by Claude Code based on the actual
  implementation it built, not aspirational descriptions of unbuilt features.
- **Verification discipline**: Claude Code ran the backend test suite, `ruff` lint, the frontend `tsc`/`vite`
  build, `eslint`, and an actual `docker build` + container smoke test in this session, fixing every failure it
  encountered (a SQLite foreign-key deletion order bug, two Issue Radar keyword-matching precision bugs, and the
  frontend tooling issues above) rather than reporting untested code as finished.

No other AI coding tools were used. There was no separate manual-implementation phase this document is
summarizing after the fact — the tool usage described above **is** how the submission was built, end to end, in
this session.
