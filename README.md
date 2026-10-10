English | [简体中文](README.zh-CN.md)

# Document Requirement Maintenance (DRM)

> Deterministic, approval-gated maintenance for existing Word requirement documents.

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-Apache--2.0-D22128)
![Status](https://img.shields.io/badge/status-MVP-blue)

Document Requirement Maintenance (DRM) is a local Python tool for inspecting, searching, comparing, planning, approving, applying, and validating small changes to Word requirement documents. DOCX remains the source of truth; exported Markdown is only a reading aid.

The current MVP supports deterministic paragraph insertion and replacement. It never defines OFS ALM business rules and does not modify a DOCX during `inspect`, `search`, or `plan`.

## Repository policy

This public repository contains source code, synthetic fixtures, and documentation only. Real requirement documents, their extracts, generated output DOCX files, and potentially sensitive plans stay outside Git by default. See [`.gitignore`](.gitignore) before the first commit.

## Install

Use Python 3.11 or newer.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
```

For repository-only execution without installation:

```powershell
$env:PYTHONPATH = "src"
python -m drm --help
```

## Workflow

Keep business documents in a local directory such as `work/`; DOCX files and generated outputs are ignored by Git by default.

Reviewed non-sensitive plans can be stored under `plans/` for Git history. Plans containing confidential excerpts should remain under the ignored `work/` directory.

### 1 Inspect

```powershell
drm inspect work\master.docx --output-dir work\master-inspection --profile ofs-alm
```

This produces `document.md`, `structure.json`, `media_index.json`, `extraction_report.json`, and extracted files under `media/`. Anchors in `structure.json` are version-specific.

Search a code or phrase without creating an inspection directory:

```powershell
drm search work\master.docx FE250 --profile ofs-alm --output work\fe250-search.json
```

Compare two existing requirement documents without modifying either source:

```powershell
drm compare work\master-v1.docx work\master-v2.docx `
  --profile ofs-alm `
  --output work\comparison-details.json `
  --result-file work\comparison-result.json
```

### Machine-readable results

Add `--result-file <path>` to any DRM operation when it is called by an Agent or another program. The compact result always contains `schema_version`, `run_id`, `operation`, `status`, `source_document`, `findings`, `artifacts`, `warnings`, and `errors`. Detailed document content remains in the artifact files.

```powershell
drm result-schema --output work\operation-result.schema.json `
  --result-file work\result-schema-run.json
```

### 2 Plan

Create a proposal JSON using [the synthetic example](examples/fe250_synthetic_proposal.json), then anchor and validate it:

```powershell
drm plan work\master.docx `
  --proposal work\proposal.json `
  --output work\change-plan-draft.json `
  --document-version v1 `
  --profile ofs-alm
```

The planner adds the target file hash and exact anchors. A target with zero or multiple matches is marked `conflict` and cannot be approved. Generate the authoritative JSON Schema when integrating another planner:

Any `approved` value arriving in a proposal is reset to `pending`; approval is accepted only through the separate review step below.

```powershell
drm schema --output work\change-plan.schema.json
```

Every change records its source, target, operation, proposed content, rationale, evidence, confidence, approval status, and validation rules. Use `ambiguous_needs_confirmation` or `insufficient_evidence` when the business basis is not confirmed.

### 3 Review and approve

Review the full draft JSON, especially evidence, target location, confidence, and proposed wording. Approval is a separate command:

```powershell
drm approve work\change-plan-draft.json `
  --change-id FE250-DEMO-001 `
  --change-id FE250-DEMO-002 `
  --approver "reviewer-name" `
  --output work\change-plan-approved.json
```

Only the named items are approved. Conflict items cannot be approved. Ambiguous or insufficient-evidence items require the conspicuous `--allow-uncertain` flag and should normally remain pending until clarified.

`drm approve` also records a digest of every approved change. `apply` rejects a manually forged `approved` status or any protected change content modified after approval. The approval record provides deterministic integrity checks; it is not a cryptographic identity signature.

### 4 Apply

```powershell
drm apply work\master.docx `
  --plan work\change-plan-approved.json `
  --output work\master-v2.docx `
  --audit-log work\master-v2.audit.json
```

The source hash must match the plan. DRM saves to a temporary DOCX, verifies the ZIP package, checks that the source stayed unchanged, and only then publishes the output. Existing outputs are preserved unless `--force` is explicit. Skipped and pending items appear in the audit log.

### 5 Validate

```powershell
drm validate work\master.docx work\master-v2.docx `
  --plan work\change-plan-approved.json `
  --output work\master-v2.validation.json
```

A valid report means the output body and styles match the original plus approved MVP edits, the original hash matches, the DOCX opens as a ZIP package, and indexed media was preserved. Package metadata differences are reported separately from content differences.

## OFS ALM profile

`src/drm/profiles/ofs_alm.json` recognizes FE codes and terms related to detail expansion, aggregation, cash-flow events, repricing, amortization, contract fields, formulas, and balances. Its FE250 entry is only an acceptance-case label; `business_definition` is deliberately null.

Add another business profile by copying this JSON shape, choosing a new `profile_id`, and defining recognition patterns and topic terms. Do not place calculation rules in the core parser. A profile can also be supplied by file path through `--profile`.

## Tests

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

The suite creates synthetic DOCX files and verifies multi-level headings, Chinese text, merged cells, images, FE codes, exact insertion and replacement, ambiguous locations, unapproved-change detection, original-file preservation, traceability, and consecutive versions.

Create the standalone synthetic fixture with:

```powershell
python tools\create_test_fixture.py work\synthetic-master.docx
```

## Known limitations

- MVP writes paragraphs only. Tables are parsed, searched, and validated; row and cell edits are modeled but skipped as unsupported.
- Floating text-box text, field instructions, equations, bookmarks, and cross-references are preserved but not fully interpreted.
- Paragraphs containing high-risk OOXML are not replaced. Insertions beside them remain possible when their content is untouched.
- Numbering, tracked changes, comments, headers, footers, and complex style inheritance need broader Phase 2 fixtures before write support is enabled.
- Visual comparison is not a required runtime dependency. Release testing should still render representative before-and-after DOCX files to PNG and inspect every page.
- Authorization is recorded in JSON but is not cryptographically signed.

See [the architecture note](docs/architecture.md) for module boundaries and validation design.

## License

DRM is released under the [Apache License 2.0](LICENSE). This permits commercial use, modification, and redistribution while providing an express patent grant and requiring preservation of license and attribution notices.
