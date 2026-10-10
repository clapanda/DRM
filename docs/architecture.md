# DRM MVP Architecture

## Scope

The MVP is a local, deterministic DOCX maintenance pipeline. It deliberately excludes semantic rule invention, vector databases, model orchestration, and lossy whole-document reconstruction.

## Component boundaries

```text
DOCX input
   |
   +--> inspect/search --> structure.json + document.md + media index
   +--> compare --------> structural and indicator difference report
   |
   +--> plan -----------> anchored change_plan.json (no DOCX write)
                              |
                         explicit approval
                              |
   +--> apply ----------> atomic DOCX copy + audit log
                              |
   +--> validate -------> semantic, media, package, and authorization report
```

- `docx_parser.py` builds the reading-order intermediate representation. It records headings, paragraph and table anchors, cell merge metadata, styles, media hashes, and unsupported OOXML counts.
- `compare.py` performs read-only semantic block and configured indicator comparison between two DOCX versions.
- `profiles.py` adds configuration-driven recognition. The OFS ALM profile recognizes FE codes and topic terms but contains no business formulas.
- `planner.py` resolves proposed locations against one immutable target hash. Zero or multiple matches become conflicts.
- `approval.py` makes approval explicit and records the approver and selected IDs. Uncertain changes require an additional explicit flag.
- `editor.py` applies only approved, ready MVP operations to an in-memory copy and publishes through a verified temporary DOCX.
- `validator.py` derives the expected semantic snapshot independently from the original plus approved plan, then compares it with the produced DOCX. Media hashes and package-part changes are checked separately.
- `result_protocol.py` defines the compact Agent-facing result envelope. Detailed source text remains in artifacts referenced by the envelope.

## MVP edit contract

Supported edits are `insert_before`, `insert_after`, and `replace_paragraph`. Insertions inherit paragraph and first-run formatting from the target. Replacement is skipped when the paragraph contains fields, drawings, bookmarks, hyperlinks, content controls, equations, text boxes, or embedded objects.

Tables and merged-cell structure are parsed and validated but table writes are intentionally not implemented in this phase. This keeps the first editing surface small enough to verify strictly.

## Anchor model

An anchor combines reading-order position with a SHA-256 content and style fingerprint. The plan also stores the exact source-document hash. Anchors are therefore valid only for the planned document version. A later version must be inspected and planned again, which prevents stale plans from silently editing the wrong paragraph.

## Validation model

Validation checks the input hash, DOCX ZIP integrity, completion of approved items, exact semantic agreement with the authorized plan, paragraph styles and positions, image-part hashes and reference counts, and changed package parts. `docProps` changes are classified as metadata; body and resource changes are classified as content or structure.
