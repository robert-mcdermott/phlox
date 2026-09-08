# Document sources and citations

[User Guide](USER_GUIDE.md) · [Project overview](../README.md)

Wave 6 implements the document portion of F08. New answers can cite uploaded documents
and an assistant's knowledge base using stable labels such as **[S1]**. Click a citation
to inspect the captured passage, filename, chunk, character range, and capture time.
The panel marks shortened passages and documents that have changed since capture.
A registered source identifies evidence supplied during the turn; it does **not** verify
that the passage supports the answer's claim.

## Start and use it

This feature is enabled without a configuration flag, in both the default chat mode and
opt-in [reconnectable runs](RUNS.md). No new service, provider, or model configuration is
required. Stop Phlox and preserve a [backup](BACKUP_RESTORE.md) before upgrading. Normal
startup applies migration `0004_sources`, which adds two tables and nullable message
citation metadata. Existing messages remain intact; old numeric/D-prefixed citations are
not retroactively assigned sources.

Attach or reference a ready document, or enable **Search documents** for the prompt. Ask
Phlox to answer from the document and cite its sources. The model still decides whether
to cite. Click an S-number in the answer to open the source panel; Escape or **Close
source** returns to the answer. An invented label is marked **unverified** and cannot
open a source. Exporting a conversation as Markdown includes its cited excerpts in a
Sources appendix, or explicit unavailable/unverified notices.

## Identity and evidence contract

- `Source` belongs to one conversation. Its opaque ID and increasing S-number are never
  reassigned to different evidence. Identity includes document ID, chunk ordinal, full
  chunk hash, and the exact retained excerpt. Repeated retrieval of that same evidence
  reuses the label; a changed chunk or differently shortened passage receives a new one.
- SQL `Document` and `DocChunk` rows are authoritative. Vector hits are candidates only:
  ownership, readiness, scope and chunk association are checked before any passage is
  registered. Stale or unauthorized candidates are omitted without exposing their payload.
- `SourceUse` associates evidence with the accounting turn, including approval continuations
  and nested agents. Final `Message.citations` binds labels to that turn's registered IDs;
  unknown labels receive a null ID. A new answer must retrieve/reference evidence again
  to establish its own bindings, even if earlier answers cited it.
- Direct references and `search_documents` share the registry. The harness emits `sources`
  SSE catalogs; approval snapshots and durable event replay preserve the same labels.
  The frontend renders chips outside code and existing Markdown links.
- Locations are **one-based chunk numbers in the UI** and character ranges within that
  chunk. The stored offsets and ordinals are zero-based, end-exclusive. They are not PDF
  page numbers: ingestion does not yet preserve page/section coordinates reliably.

## Privacy, deletion and limits

Source reads and exports first require conversation ownership, with 404 for another
user's conversation or source ID, including admins. Every excerpt read also rechecks the
current document scope and active assistant visibility. Changing a public assistant to
private can make its prior citations unavailable to other users. The API then returns a
generic notice without the old filename or excerpt.

| Bound | Current value |
|---|---|
| Retained passage | 6,000 characters per source |
| Evidence supplied in one turn | 64 distinct sources |
| Source identities in one conversation | 512, including unavailable tombstones |
| Stored retrieval query | First 500 characters, once per source/turn |
| Snapshot availability | 30 days since the latest capture of that source |

These are code constants in `sources.py`, not admin settings. On a limit, retrieval reports
omitted evidence; it does not expose an unregistered passage. Start a new conversation
when its identity limit is reached. Reusing a source renews its expiry. Once expired, the
panel and export refuse its excerpt immediately. Snapshot text/title/location and query
context are purged at startup and hourly while the run worker is enabled. With the worker
disabled, physical cleanup occurs at the next startup; expiry checks still apply on reads.
An accessible, unchanged document can be recaptured under the same identity.

Deleting a document through Phlox atomically clears its source snapshots and retrieval
queries, leaving stable unavailable labels. Deleting a conversation or its owner's data
removes its registry and use rows. Assistant knowledge-base deletion uses the same purge.
The supported single-process mutation lock serializes captures with document deletion;
direct SQL writers bypass the ORM deletion hook and are unsupported.

This is a **source snapshot policy**, not transcript redaction: answers, tool results,
pending context, run events, previously downloaded exports, and backups may already contain
quoted document text. Deleting a document does not erase those historical copies. Apply the
existing conversation/account deletion and backup retention policies separately. Export
reauthorization governs the source appendix; it does not rewrite historical answer text
or tool arguments. A passage already displayed or downloaded cannot be remotely withdrawn.

## API and extension seams

- `GET /api/conversations/{conversation_id}/sources/{source_id}` returns either an
  available snapshot or `{id, label, available: false, reason}`.
- `GET /api/conversations/{conversation_id}/export` returns `{markdown: "..."}`, with
  source access rechecked at export time. No bearer URLs or source credentials are embedded.
- `app.sources.capture` registers canonical document evidence before a tool gives its
  label to a model. `catalog`, `bind`, and `inspect_source` handle turn references and reads.

This delivery covers uploaded documents and assistant knowledge bases. Web discovery/
fetched-page evidence, fetch failures/paywalls, PDF page coordinates, claim-support
scoring, and artifact-version citation metadata remain later work. The generic kind/URL
fields reserve an extension seam; they do not imply that web citations are captured today.
