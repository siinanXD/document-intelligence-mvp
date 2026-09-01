# Machine Intelligence architecture (SIN-88)

This document is the evidence-backed plan for Machine Intelligence. It validates
the Linear baseline
[Machine Intelligence — Ingestion and Adapter Architecture Baseline](https://linear.app/sinan-kahraman/document/machine-intelligence-ingestion-and-adapter-architecture-baseline-466358b39d5f)
against the current `siinanXD/document-intelligence-mvp` codebase, existing
siinanXD repositories, and open-source parser/standard capabilities.

**This issue ships documentation only.** It does not add production Machine
Intelligence schema, adapters, or parsers. Implementation starts at
[SIN-89](https://linear.app/sinan-kahraman/issue/SIN-89/define-canonical-machine-assembly-and-engineering-evidence-model)
after this document is merged.

## Outcome in one page

The current stack can satisfy the first vertical slice. Do not add a graph
database, a second upload pipeline, an orchestration framework, or a new AI
provider.

The first slice is a **synthetic multi-conveyor material-handling line** whose
PLC source of truth is **already-exported Siemens SimaticML XML plus readable
SCL/AWL listings and tabular tag/I/O lists**. Runtime workers never require
TIA Portal, EPLAN, or STEP 5 to be installed. Native opaque projects
(`.zap`, `.elk`, `.S5D`) are deferred.

Adapter families for that slice:

| Family | Classification |
|---|---|
| PDF / DOCX / Markdown / text via Docling | supported in first slice |
| XLSX BOM, I/O, cable and terminal lists | supported in first slice |
| SCL / AWL / listing / symbol / cross-ref text | supported in first slice |
| SimaticML XML (custom stdlib adapter) | supported in first slice |
| PNG / JPEG as photographic evidence | supported in first slice (quality warnings; OCR experimental) |
| TIA / S7 native project or Openness worker | supported via required export |
| EPLAN native project / API | supported via required export (PDF + XLSX lists) |
| S5 / STEP 5 binary project | supported via required export (readable text only in first slice) |
| PLCopen XML / IEC 61131-10 | experimental |
| AutomationML `.aml` | experimental |
| OCR on scans | experimental |
| Pneumatics / hydraulics | deferred |
| DXF / DWG as primary schematic | deferred |
| Live TIA Openness or EPLAN API in workers | deferred |

Exact TIA/S7 fixture format: **SimaticML block XML + hardware/tag/I/O
spreadsheets + SCL source listings**, authored synthetically so CI never
needs licensed Siemens software. See [Exact TIA/S7 fixture format](#exact-tias7-fixture-format).

## What was inspected

### This repository (`document-intelligence-mvp` at `59d6858`)

| Area | Finding |
|---|---|
| Schema | `tenants`, `documents`, `chunks`, `document_profiles`, `document_relations`, `ingestion_jobs`. No machine, assembly, PLC, signal, or evidence-reference tables. |
| Provenance | Chunks carry `page_number`, `section_title`, `source_id` (`{document_id}:{ordinal:05d}`), and JSONB `source_metadata`. Bounding boxes live in `source_metadata`, not a dedicated column. |
| Vectors | Qdrant payloads are identifiers only. PostgreSQL remains source of truth for text. |
| Parser seam | `DocumentParser` in `app/providers/parsing.py`. `get_document_parser()` always returns `DoclingParser`. There is no MIME→parser router. |
| Upload gate | `app/services/uploads.py` accepts pdf/docx/pptx/xlsx/html/md/txt. ZIP, XML, SCL, AWL, CSV, PNG and JPEG are refused today. |
| Ingestion | `POST /documents` → `ingest_upload` → worker `process_job`: parse, `content_hash`, `{storage_key}.docling.json`, chunks, index, profile, relations. |
| Storage | `StorageBackend` (`put`/`get`/`delete`/`exists`) is tenant-keyed and sufficient for originals plus unpacked members. |
| Evaluation | `app/evaluation/` plus `evaluation/datasets/`. New tracks add a dataset directory; they must not replace the package. Fixtures are synthetic text rendered to PDF/DOCX/XLSX with the standard library (`app/evaluation/formats.py`), no `openpyxl`. |
| Tenant isolation | Every row has `tenant_id`. Uniqueness is per tenant. Composite FKs. Cross-tenant access is a bug; each read path needs a negative test. |
| Layering | `api → services → providers`. Routes never talk to Qdrant, object storage, or an AI vendor. |

Docling is sufficient for born-digital manuals, datasheets, DOCX and PDF text
with layout. It is **not** a PLC XML parser, a native EPLAN connectivity
reader, or a ZIP package intake.

### Other siinanXD repositories (patterns only)

Inspected at README / stated-purpose level. None of these architectures is
copied into this product.

| Repo | Reuse | Do not copy |
|---|---|---|
| `ai-template-document` | Drop ungrounded extraction fields; compute `requires_review` server-side from missing evidence. It already names this repository as the primary reference. | Workers, tenant model, Qdrant, Docling pipeline. |
| `ai-template-rag` | Nothing architectural. | pgvector. This product stays on Qdrant plus PostgreSQL. |
| `ai-starter` / `ai-core` | Nothing. Grounded generation already lives here (SIN-76 / SIN-74). | Replacing `app/providers` with a thinner stack. |

### Open-source parsers and standards

See the [Parser and standard evaluation matrix](#parser-and-standard-evaluation-matrix).
Headline results:

* Opaque TIA `.zap` / project files are not assumed parseable.
* TIA Openness is proprietary, Windows-hosted, and licensed. Runtime must not
  depend on TIA being installed.
* Preferred CI/runtime input is **already-exported SimaticML XML**, plus tag
  tables and hardware/I/O as CSV/XLSX, plus SCL/source listings.
* `simaticml-decoder` (Czarnak) is MIT and translates exported SimaticML
  LAD/FBD → SCL+JSON. It is immature (single-digit stars, TIA V21-oriented).
  First-slice product path is a **custom stdlib XML adapter**. The decoder is
  an experimental research reference, not a required dependency.
* PLCopen XML (`plcopen` on PyPI / IEC 61131-10) is vendor-neutral. Siemens TIA
  does not typically emit it as the primary export. Optional if a fixture
  member is already PLCopen; do not make the first fixture PLCopen-only.
* Avoid `plcforge` / `plcopener` (non-commercial licences) on the product path.
* S5 first slice is **readable AWL / listing / symbol / cross-ref text**.
  Binary `.S5D` is deferred. `DotNetSiemensPLCToolBoxLibrary` is C# and not a
  Python runtime dependency. `awlsim` is an S7 AWL simulator (GPL-2), not an
  S5 binary decoder.
* Native EPLAN `.elk` / project DB is proprietary. EPLAN API requires installed
  EPLAN. First slice: structured PDF through existing Docling plus tabular
  device/cable/terminal XLSX exports. AutomationML `.aml` is experimental if a
  cleared sample exists.

## Baseline validation

The Linear baseline is accepted as the implementation hypothesis, with these
audit refinements:

| Baseline claim | Audit result |
|---|---|
| Reuse this pipeline; no second document stack | **Confirmed.** Extend `uploads` → storage → worker → evaluation. |
| PostgreSQL is the canonical relationship store | **Confirmed.** Current tables cannot represent machines/signals/PLC; SIN-89 adds tables in Postgres. No graph database. |
| No new provider / orchestrator unless the audit proves need | **Confirmed.** Existing embeddings, LLM, storage and Docling cover the first slice. Adapters are a new **interface family** in this repo, not a vendor SDK. |
| Native structured exports beat rendered documents | **Confirmed.** SimaticML and XLSX outrank PDF geometry for connectivity and PLC. |
| Docling for PDF/DOCX | **Confirmed**, with an explicit limit: Docling does not parse PLC XML or native EPLAN. |
| TIA Openness XML as preferred input | **Refined.** Openness is the *authoring* path, not the runtime path. Canonical input is the **exported XML/files**, not a live Openness worker. |
| PLCopen XML as second preferred TIA input | **Refined.** Experimental / optional. Do not require it for the first fixture. |
| Opaque TIA archives not assumed parseable | **Confirmed.** |
| S5 binary is a separate capability gate | **Confirmed.** Readable exports only in the first slice. |
| EPLAN native export preferred over PDF | **Refined for first slice.** Native EPLAN is proprietary. First slice uses **structured PDF + XLSX lists**. Native API is deferred. |
| ZIP/folder containers | **Confirmed as SIN-100 work.** Today `.zip` is rejected (`415`). Safe unpack belongs on the existing upload/storage path. |
| LLM may classify unresolved cases but must not invent connectivity or PLC behavior | **Confirmed.** Same “drop ungrounded fields” rule as document Q&A. |
| Large conveyor-line fixture (10–12 sections, ≥150 I/O, 25–40 schematic pages) | **Confirmed as SIN-99 scope.** Fast CI subset + full profile sharing one oracle. |

No baseline item required a new database engine, Qdrant replacement, or agent
framework.

## Non-negotiable principles

Inherited from the baseline and from `CLAUDE.md`, restated so later issues do
not invent a second architecture:

1. Preserve every uploaded artifact immutably and fingerprint it before
   interpretation (`file_hash` already exists; package members need the same).
2. Native structured exports beat rendered documents; deterministic parsers
   beat LLM extraction.
3. Every entity, relation, package assignment and behavior claim carries
   method, parser/model version, confidence and a resolvable evidence locator.
4. Never silently merge ambiguous machines, components, symbols or revisions.
5. Unsupported constructs are explicit outputs, not ignored input.
6. Human correction is an auditable override; it never destroys original
   evidence or the prediction it replaced.
7. PostgreSQL remains the canonical graph/relationship store for the first
   vertical slice.
8. No new AI provider, graph database or orchestration framework.
9. LLMs may classify unresolved cases and explain normalized logic. They must
   not invent connectivity or PLC behavior. Absent information stays null or
   empty.
10. Tenant isolation is unchanged: every new row carries `tenant_id`; every
    query takes it as an explicit argument; each new read path ships a
    cross-tenant negative test. Uniqueness is per tenant.
11. Log identifiers (`tenant_id`, `document_id`, `job_id`, later
    `package_id`), never document content, extracted entities, filenames of
    customer data, prompts or completions.
12. Tests mock every paid external call.

## Current stack is sufficient

| Proposed addition | Needed for first slice? | Decision |
|---|---|---|
| Neo4j / Neptune / other graph DB | No | Defer. Postgres tables + composite FKs. |
| Temporal / Celery / new orchestrator | No | Existing `ingestion_jobs` worker. Package intake is extra job steps, not a new runner. |
| New embedding or LLM provider | No | Existing registry. |
| pgvector | No | Qdrant stays. Long text remains searchable; machine facts are relational. |
| `openpyxl` | Not in SIN-88 / SIN-89 | Evaluation already builds XLSX with stdlib. A later tabular adapter may add it; do not add it here. |
| Docling replacement | No | Keep for PDF/DOCX/PPTX/HTML. Route XML/listings/images around it. |
| Parallel upload service | No | Extend `SUPPORTED_TYPES`, `receive_upload`, and storage keys. |

## Extension points in this codebase

Machine Intelligence plugs into these seams. Later issues must not create a
parallel pipeline.

```
HTTP upload (api/documents.py)
  → receive_upload / validate_upload (services/uploads.py)
  → StorageBackend.put (original bytes, immutable)
  → ingestion_jobs
  → process_job (services/processing.py)
       → DocumentParser.parse          # text + chunks for search
       → content_hash / normalized_key
       → index_document / profile / relations
       → [SIN-100] adapter.detect/extract/validate  # typed observations
       → [SIN-90+] assignment / resolution services
```

| Seam | File | What SIN-100 / SIN-89 should do |
|---|---|---|
| Upload allow-list | `app/services/uploads.py` `SUPPORTED_TYPES` | Add zip (container only), xml, csv, scl/awl/txt listings, png/jpeg. Keep magic-byte + extension agreement. |
| Parser registry | `app/providers/registry.py` `get_document_parser()` | Keep Docling as the document parser. Add a **separate** adapter registry; do not force PLC XML through Docling. |
| Parser interface | `app/providers/parsing.py` | Continues to mean “bytes → citable text chunks”. Adapters are not `DocumentParser` implementations. |
| Job processing | `app/services/processing.py` `process_job` | After a successful parse (or an explicit “search-skippable structured artifact” path), call adapters. One transaction still owns one artifact. Package-level assignment is a later job/step, not a second worker binary. |
| Normalized artifact | `normalized_key_for()` | Today `{storage_key}.docling.json`. Structured adapters store their own serialized observations beside the original, with a parser/adapter name on the row. |
| Provenance | `chunks.source_id`, `source_metadata` | Search citations stay here. Machine-model evidence locators are first-class rows in SIN-89, and may *point at* chunk `source_id`s when the fact came from prose. |
| Profiles | `document_profiles` | Too document-centric. Do not overload with machines/signals. |
| Document relations | `document_relations` | Keep for duplicate/version/same-case. Machine connectivity is new tables. |
| Storage | `app/providers/storage.py` | Store unpacked zip members as additional tenant-scoped keys. Original zip remains immutable. |
| Evaluation | `app/evaluation/` + `evaluation/datasets/` | Add `machine-intelligence-v1` (name may be refined in SIN-99). Do not replace retrieval/generation tracks. |
| Cockpit | `web/` | Later surfaces. No MI UI in this issue. |

Adapters **must not** write final canonical entities. They emit typed
observations. SIN-90+ services resolve those into canonical rows.

### Adapter contract (SIN-100)

Every adapter implements:

* `detect(artifact) -> detection score + reasons`
* `extract(artifact, context) -> normalized observations + evidence + warnings`
* `validate(result) -> deterministic validation report`
* `capabilities() -> formats, versions, constructs and limitations`

Identical input + adapter version must produce identical observations.
Type detection in core CI is deterministic (MIME, magic, XML root/namespace,
filename suffix). LLM classification is allowed only as a weak signal after
deterministic routing, and never as a required CI path.

## Parser and standard evaluation matrix

Decision key: **reuse** existing code, **integrate** a library, **adapt** a
standard with a small custom parser, **custom-build**, **defer**.

| Input | Standard / format | Candidate tooling | Licence / maturity | Evidence locator | Decision | First-slice class |
|---|---|---|---|---|---|---|
| Born-digital PDF, DOCX, MD, TXT, HTML, PPTX | Existing allow-list | Docling (`DoclingParser`) | BSD-3, already vendored as `parsing` extra | page, section, bbox in `source_metadata` | **reuse** | supported in first slice |
| XLSX BOM / I/O / cable / terminal | OOXML spreadsheet | stdlib zip/xml now; optional `openpyxl` later | Evaluation already builds XLSX without extra deps | workbook, sheet, row, cell, range | **adapt** (stdlib first) | supported in first slice |
| CSV / TSV lists | RFC 4180 | stdlib `csv` | — | file, row, column | **adapt** | supported in first slice (secondary to XLSX) |
| SCL / ST source | IEC 61131-3 text | custom tokenizer/parser | — | file, line range, POU name | **custom-build** | supported in first slice |
| AWL / IL listings | Siemens instruction list text | custom line parser | — | file, line range, block | **custom-build** | supported in first slice |
| SimaticML XML (exported blocks) | Siemens TIA XML export | custom `xml.etree` adapter; `simaticml-decoder` MIT, immature | MIT decoder is research-only | file, XML path, block/network, instruction | **custom-build** | supported in first slice |
| Tag table / hardware / I/O export | TIA CSV/XLSX export | tabular adapter | — | sheet/cell or CSV row | **adapt** | supported in first slice |
| TIA Openness live API | Proprietary Siemens API | Requires licensed TIA on Windows | Proprietary | n/a at runtime | **defer** (offline authoring only) | supported via required export |
| Opaque TIA `.zap` / project | Proprietary | none suitable | Proprietary | n/a | **defer** | supported via required export |
| PLCopen XML | IEC 61131-10 | `plcopen` on PyPI if licence/API fit | Verify before integrate | XML path, POU, variable | **adapt** later | experimental |
| `plcforge` / `plcopener` | PLCopen-related | non-commercial / restricted | **do not use** on product path | — | **defer** | deferred |
| S5 AWL / listing / symbol / XREF text | STEP 5 printable exports | custom text adapter | — | file, line, symbol | **custom-build** | supported via required export (text only) |
| S5 binary `.S5D` | Proprietary binary | `DotNetSiemensPLCToolBoxLibrary` (C#) | Not a Python dep; licensing/maturity unproven for SaaS | — | **defer** | deferred |
| `awlsim` | S7 AWL simulator | GPL-2 simulator, not an S5 decoder | GPL-2 — do not put on product path | — | **defer** | deferred |
| EPLAN structured PDF | PDF with page names / title blocks | Docling | existing | page, section, bbox, title-block text | **reuse** | supported via required export |
| EPLAN device/cable/terminal XLSX | Tabular export | tabular adapter | — | sheet/cell | **adapt** | supported via required export |
| EPLAN native `.elk` / API | Proprietary | EPLAN API, installed EPLAN | Proprietary | native object ids | **defer** | deferred |
| AutomationML `.aml` | IEC 62714 | experimental XML adapter if a cleared sample exists | Standard; samples must be cleared | XML path, object id | **adapt** later | experimental |
| PNG / JPEG photos | Raster | store original + quality metadata; OCR later | — | image, region | **custom-build** (quality first) | supported in first slice (OCR experimental) |
| Scanned PDF OCR | Docling `do_ocr` | existing flag | — | page image + OCR span | **reuse** experimental | experimental |
| ZIP / folder package | Container | stdlib `zipfile` with traversal/size limits | — | relative path inside archive | **custom-build** in SIN-100 | supported in first slice (container) |
| DXF / DWG | CAD | various; licence and geometry complexity | — | — | **defer** | deferred |
| Pneumatic / hydraulic diagrams | Various PDF/CAD | — | — | — | **defer** | deferred |

GPL-only and non-commercial tools stay off the runtime and CI product path.
Research notes may mention them; `pyproject.toml` must not.

## Adapter family classification

Required by SIN-88: each family is exactly one of **supported in first slice**,
**supported via required export**, **experimental**, or **deferred**.

### A. Container and manifest

**Supported in first slice.** SIN-100 adds safe zip unpack on the existing
upload path: traversal rejection, recursion limit, member-count and expanded-
size limits, preserved relative paths as hints (never truth), exact-duplicate
fingerprints, version candidates without silent merge. The original archive
stays immutable in object storage. Folder-only uploads are a later convenience;
the fixture is a zip of relative paths.

### B. Siemens TIA / S7

**Supported via required export**, with the exported members themselves
**supported in first slice**. Runtime consumes:

1. SimaticML XML for blocks (LAD/FBD structure when the XML represents it)
2. SCL source listings
3. Tag tables, assignment lists, hardware/I/O configuration as XLSX/CSV
4. Cross-reference reports as text or spreadsheet
5. PDF/print reports only as fallback prose, never as the PLC source of truth

Not consumed at runtime: live Openness, `.zap`, installed TIA.

### C. Legacy Siemens S5 / STEP 5

**Supported via required export** (readable text only). First slice may include
an **optional** S5 listing fixture for a separate capability test. It must not
block TIA/S7. Binary `.S5D` and installed STEP 5 are **deferred**.

Readable-export boundary (accepted):

* `.awl` / `.STL` / `.txt` instruction-list exports
* symbol / assignment lists (text or CSV/XLSX)
* cross-reference and hardware-configuration print exports as text

Rejected until a later issue:

* `.S5D` / `.S5P` / other binary project containers
* any library that requires STEP 5 or a .NET runtime in the worker

### D. Generic IEC 61131-3 / PLCopen XML

**Experimental.** Accept PLCopen XML when a package member is already that
format. Do not generate or require it for the conveyor-line fixture. Vendor
extensions remain evidence-backed metadata, not a second canonical model.

### E. PDF and document

**Supported in first slice** via existing Docling. Long text stays searchable.
Only structured, source-backed observations enter the machine model.

### F. Spreadsheet and tabular

**Supported in first slice** for XLSX (and CSV). Every value resolves to
workbook/sheet/cell or file/row/column. Workbooks are never flattened into
untraceable plain text for the machine model (search chunks may still exist).

### G. Image and mobile-photo

**Supported in first slice** as evidence with quality assessment (blur, skew,
glare, crop, resolution). OCR and geometric wiring inference are
**experimental**. A photo must not be treated as exact connectivity unless
confidence and validation support it.

### H. Electrical schematic

**Supported via required export.** First slice uses:

1. Structured EPLAN-style PDF (page names, title blocks, navigation text) through Docling
2. Device, cable and terminal lists as XLSX
3. Generic born-digital schematic PDF as a weaker fallback

Native EPLAN connectivity/object export and the EPLAN API are **deferred**.
Rendered PDF geometry never outranks a structured list when both exist.

### I. Pneumatic / hydraulic

**Deferred.** Same evidence contract later; not in the first conveyor-line
acceptance path except as an optional documented actuator with position
feedback if the electrical/PLC side already models it.

## Exact TIA/S7 fixture format

Locked for SIN-99 and SIN-93:

| Member | Format | Why |
|---|---|---|
| Block logic | SimaticML XML (`*.xml`) with Siemens namespaces as produced by a TIA block export | Structured, diffable, no live TIA |
| Structured text | `.scl` listings for the same POUs where SCL exists | Readable oracle for conditions/assignments |
| Tags / symbols | XLSX (and/or CSV) with name, data type, address, comment | Tabular adapter, cell-level evidence |
| Hardware / I/O | XLSX device and channel list (S7-1500-class labels, central + distributed I/O) | No binary hardware config |
| Cross-references | CSV or text report of selected read/write locations | Oracle for SIN-93 |
| Fallback only | PDF print of a block | Must not be the only PLC evidence |

**Not in the fixture:** `.zap`, `.ap17`/project directories, live Openness
payloads, encrypted libraries.

Authoring default: **hand-authored / generator-produced synthetic SimaticML**
checked into `evaluation/datasets/` (exact path chosen in SIN-99). CI and
Railway smoke never install TIA.

Optional offline authoring: a human with a licensed TIA copy may export
real SimaticML to seed the generator. That is a laptop tool, not a worker
dependency. See [Human decisions](#human-decisions).

## EPLAN native-versus-PDF strategy

First slice does **not** parse native EPLAN projects.

| Priority | Input | Role |
|---|---|---|
| 1 | Device, cable, terminal, I/O XLSX exports | Canonical connectivity and designations when present |
| 2 | Structured multi-page schematic PDF | Page identity, title blocks, reference designators, cross-page text |
| 3 | Datasheets / manuals (PDF/DOCX) | Technical properties with citations |
| 4 | Scan / photo | Evidence with quality warnings |

If a future cleared AutomationML or native XML export appears, it outranks PDF
geometry. It does not outrank an already-ingested XLSX list for the same
object without an explicit conflict record.

## First vertical slice and acceptance fixture

Implemented in **SIN-99**, not this PR. SIN-88 locks the shape so SIN-99 does
not invent a second product.

### Machine

A synthetic **multi-conveyor material-handling line** (not a three-sensor toy):

* 10–12 interacting conveyor sections in multiple assemblies/zones
* ≥150 combined digital/analog PLC I/O points
* one S7-1500-class CPU fixture, central + distributed I/O
* multiple program blocks, reusable conveyor/motor FBs, data blocks, alarm/fault subsystem
* 25–40 schematic pages with real cross-page references
* multiple cabinets/locations and more than one supply/distribution page
* repeated component types so identity resolution cannot rely on unique names only

Drive and sensor diversity, electrical page coverage, PLC modes, and the seven
end-to-end scenarios follow the Linear baseline (startup, downstream blocked,
jam/missing speed, VFD fault, protection-feedback mismatch, sensor failure,
reset/restart). Safety signals exist for traceability testing only; the
product must not claim functional-safety certification.

### Package members (minimum)

* machine/assembly overview (DOCX or Markdown)
* electrical schematic PDF (multi-page, synthetic)
* TIA/S7 SimaticML XML + SCL listings + hardware/tag/I/O XLSX
* signal / cable / terminal lists (XLSX)
* alarm/fault list (XLSX or Markdown)
* BOM with quantities and reference designators (XLSX)
* motor/drive assignment table (XLSX)
* commissioning checklist (Markdown)
* selected device facts as structured JSON plus citation URLs — **do not
  redistribute copyrighted manufacturer manuals**
* one scanned-page image or representative mobile photo (PNG/JPEG)
* revision/change record
* one unrelated artifact
* one modified/revised artifact
* one ambiguous or conflicting mapping
* optional separate S5 readable-export fixture (does not block TIA/S7)

Known chain the oracle must include (at least three complete chains):

`input condition → PLC logic → output address → terminal/electrical path → actuator → expected sensor feedback`

### Dataset layout

Follow `docs/EVALUATION.md`: a new versioned directory under
`evaluation/datasets/` (SIN-99 chooses the name, e.g.
`machine-intelligence-v1`). Fast CI subset and full acceptance profile **share
the same oracle and generator version**. Retrieval-v1 / generation-v1 stay
untouched.

Controlled mutations (renames, shuffle, missing title block, degraded photo,
duplicate, old vs current BOM, reused tag, unrelated mix-in) are fixture
variants, not separate architectures.

### Licensing

Hand-authored synthetic files and generator output are committed. Manufacturer
datasheets are cited by URL + retrieved structured facts, not copied as PDFs,
unless a specific file is cleared for redistribution. xPPU and other public
research plants stay **out of CI** (`docs/EVALUATION_DATASETS.md`).

## Deferred formats and features

Explicitly out of the first slice (follow-up issues, not silent scope creep):

* TIA Openness in production workers or Railway
* Opaque TIA project / `.zap` parsing
* Native EPLAN API / `.elk` / project database
* S5 binary `.S5D` decoding
* Pneumatic/hydraulic diagram understanding as a primary adapter
* DXF/DWG as the schematic source of truth
* Live PLC online debug / download
* Functional-safety certification claims
* Graph database
* New embedding/LLM vendor
* Replacing Qdrant with pgvector
* Agent/orchestration frameworks
* Customer document filenames or contents in logs
* Production authentication (already tracked as SIN-98; orthogonal)

## Canonical objects (SIN-89)

SIN-89 adds the smallest Postgres model that can hold the fixture oracle.
Existing `documents` / `chunks` are reused, not duplicated.

Logical objects (table names are SIN-89’s to choose):

* Artifact / ArtifactVersion (may start as `documents` plus package membership)
* MachinePackage, PackageAssignment
* Machine, Assembly
* EngineeringEntityCandidate, canonical EngineeringEntity
* Component, Signal, Port/Terminal, Cable/Connection
* PLCProgram, POU/Block, Network, Variable/Symbol, IOAddress, ReadWriteReference, LogicCondition
* RelationCandidate, canonical EngineeringRelation
* EvidenceReference (page/bbox, sheet/cell, image region, XML path/line, native id)
* Conflict, UnsupportedConstruct, HumanOverride
* DerivedBehaviorClaim with a complete dependency/evidence path

Rules for SIN-89:

* Every tenant-owned row carries `tenant_id`.
* Uniqueness is per tenant (and per package where needed).
* Composite FKs follow the `chunks` pattern.
* Vendor-specific fields do not dominate the core model; Siemens addresses and
  EPLAN designations are typed metadata on entities/evidence.
* Candidates and canonical rows are distinct. Ambiguity is data, not a crash.
* Do not overload `document_profiles` or `document_relations` for this graph.

## Implementation order after this document merges

Linear already encodes: SIN-88 blocks SIN-89, SIN-99, SIN-100 and SIN-93.
SIN-100 is also blocked by SIN-89 and SIN-99.

Recommended execution:

1. **SIN-89** — canonical Postgres model, Alembic, tenant-isolation tests.
   Can proceed immediately after merge. Does not need the full zip fixture.
2. **SIN-99** — versioned reference package + machine-readable oracle. May
   proceed in parallel with SIN-89 once object names in this document are
   stable; the oracle should use the SIN-89 table/field names as they land.
   If they land in parallel, SIN-99 targets the object list above and
   follows up if SIN-89 renames columns.
3. **SIN-100** — adapter framework + safe zip unpack on the **existing**
   upload/storage/worker path. Needs SIN-89 types (where observations land)
   and SIN-99 bytes (what routing is tested against). Capability stubs for
   TIA XML, S5 text, schematic PDF, tabular, image, Docling; full domain
   parsers stay in later issues.
4. **SIN-90** — classification and package assignment.
5. **SIN-91 / SIN-92** — entities and connectivity.
6. **SIN-93** — TIA/S7 normalized PLC path (SimaticML + listings).
7. **SIN-94** — PLC-to-physical mapping.
8. **SIN-95** — deterministic behavior chains.
9. **SIN-96** — evaluation gates on the SIN-99 oracle.
10. S5 binary / native EPLAN / Openness workers only after the readable-export
    path passes and a later audit proves a legally usable parser.

Do not start SIN-100 before SIN-89 and SIN-99. Do not implement production MI
parsers in SIN-89. Do not broaden the first slice because an extra format can
be listed.

## Proprietary tooling and licensing

Identified **before** implementation continues:

| Tool | Required at runtime? | Required in CI? | Notes |
|---|---|---|---|
| TIA Portal / Openness | **No** | **No** | Optional offline fixture authoring by a human. |
| EPLAN Electric P8 / API | **No** | **No** | First slice is PDF + XLSX. |
| STEP 5 | **No** | **No** | Text exports only. |
| Siemens licence for SimaticML schema | Runtime parses **exported files** the customer already produced. We do not redistribute TIA. Synthetic XML in CI is original work. | | |
| `simaticml-decoder` | **No** | **No** | MIT, immature; optional research. |
| GPL simulators (`awlsim`) | **No** | **No** | Stay off the product path. |
| Non-commercial PLC tools | **No** | **No** | Stay off the product path. |
| Docling | Worker extra, already used | Parser tests already gated | Keep. |
| OpenAI | Existing optional provider | Mocked in CI | Unchanged. |

Customer-supplied TIA/EPLAN exports in production are the customer’s licence
problem, not a reason to install those IDEs on Railway.

## Human decisions

Cursor must stop for a human when a native format requires proprietary
tooling, when two parsers would change architecture, or when fixture ground
truth cannot be established. This audit resolves those as follows:

| Question | Decision | Needs a further human stop? |
|---|---|---|
| Live TIA Openness in workers? | **No.** Exports only. | No, unless the owner later demands live Openness. |
| Licensed TIA to *author* the fixture? | **Optional and offline.** CI default is synthetic XML. | **Yes, if** the owner insists the oracle must be produced from a real TIA project rather than a generator. Until then, synthetic is the plan. |
| Native EPLAN vs PDF? | PDF + XLSX lists for first slice. | No. |
| S5 binary in first slice? | **No.** | No. |
| `simaticml-decoder` as a product dependency? | **No.** Custom XML adapter. | No. Switching to it later is an isolated adapter change, not an architecture change. |
| PLCopen as the fixture PLC format? | **No.** | No. |
| Graph database? | **No.** | No. |
| Can ground truth be established without vendor IDEs? | **Yes**, via a generator and a committed oracle. | Only if synthetic SimaticML is rejected as insufficiently “real”. |

Implementation of SIN-89 may proceed on these decisions without waiting.

## What SIN-88 does not do

* No Alembic revisions, ORM tables, or adapters.
* No zip upload support yet (documented for SIN-100).
* No fixture files yet (SIN-99).
* No changes to retrieval/generation evaluation corpora.
* No Railway or TIA/EPLAN installation.
* No live provider calls.
