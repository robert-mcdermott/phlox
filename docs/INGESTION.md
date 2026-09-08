# Document processing and search maintenance

[User Guide](USER_GUIDE.md#documents-search-and-citations) · [Sources and citations](SOURCES.md)

Phlox processes uploaded documents in a database-backed queue. This worker is always
available in the supported single-process deployment, independently of `runs.enabled`.
It requires no additional service. The existing embedded Qdrant default still works.

## Upload, progress, and retry

Upload in **Settings → Documents**, attach a document in chat, or add assistant knowledge
in **Settings → Assistants**. Processing shows queued, extracting, embedding, indexing,
then ready. Embedding progress counts chunks for the current attempt. Only ready documents
participate in retrieval. Upload requests return once the file is saved and queued.

Use **Retry processing** after an error or interruption. Use **Reprocess document** on a
ready file to extract its original upload again with the current parser and embedder.
Both start a new attempt; successful processing replaces the document's SQL chunks instead
of appending duplicates. Uploading the same file separately still creates a separate document.
If the original upload is missing, upload it again. Reprocessing temporarily removes that
document from retrieval; a failed attempt stays unavailable until a successful retry.

On restart, unfinished uploads, queued documents, and active processing become
**interrupted**. Retry explicitly; Phlox does not automatically repeat paid embedding calls.
An interrupted index rebuild also needs an explicit retry. Deleting a document prevents
its worker from publishing further results. There is no separate pause/cancel button for
document processing; chat **Stop** controls the chat run, not the document worker.

## Upgrade an existing library

1. Stop Phlox and preserve a [verified backup](BACKUP_RESTORE.md).
2. Update dependencies/build as usual, then start normally. Checked migration
   `0005_ingestion` adds nullable processing, provenance, and embedding-identity metadata.
   Existing chats, files, chunks, and source snapshots remain intact.
3. Existing chunks have **unknown embedding identity**. Search uses authorized keyword
   matching until an admin opens **Settings → Documents → Rebuild search index**. This
   explicitly embeds all ready passages using the configured embedding model. New uploads
   cannot join an incompatible/unknown ready library; rebuild first, then retry those uploads.
4. To obtain PDF page numbers and DOCX table locations for older files, use **Reprocess
   document** after rebuilding. Rebuilding embeddings alone does not reparse original files.
   Old citations keep their retained evidence; newly captured evidence uses the new locations.

## Embedding configuration and degraded search

The file-only `embeddings` section selects an OpenAI-compatible embedding profile:

```yaml
embeddings:
  profile: local-ollama
  model: nomic-embed-text
  # Bump this when a provider replaces a model behind the same model ID.
  version: '1'
```

Restart after file changes. Phlox records the profile, provider, model, operator-supplied
version, endpoint hash, and dimensions. It detects model changes even when dimensions stay
the same. It cannot detect a provider silently replacing weights behind an unchanged
endpoint/model/version; increment `version` in that case, then rebuild. API secrets are not
stored in the identity. Configured providers receive document passages and search queries.

With no embedding profile, Phlox deliberately uses deterministic **local-hash** vectors;
these are dependency-light and are not semantic embeddings. With a configured profile,
embedding errors never silently substitute hash vectors. Incompatible identities, invalid
vectors, missing indexes, and provider/index failures trigger SQL **keyword-only search**.
The search tool and direct-reference context receive an explicit degraded-mode notice,
including on an empty result. Keyword search may miss paraphrases. The admin index panel
shows configured mode/identity compatibility and the latest rebuild outcome; it is not a
continuous provider or Qdrant health probe.

**Rebuild search index** always re-embeds all ready passages and can incur provider costs.
It builds a separate Qdrant collection, verifies the current library/settings, then commits
new SQL vectors and the active collection name together before switching the process pointer.
Failure preserves the previous published index and saved vectors. If configuration changed,
that old index remains preserved but keyword search is used until a compatible rebuild.
Rebuilds and ingestion execute serially in one worker; rebuilds take priority over queued uploads.

The offline `uv run -m app.ops reindex` command, run from `backend/` with Phlox stopped,
rebuilds from **saved vectors** without provider calls. It does not establish unknown
embedding identity or switch models. See [restore guidance](BACKUP_RESTORE.md).
Cross-conversation memory retains its separate, older embedding path in this wave.

## Extraction and limits

- Text-layer PDFs retain one-based page numbers. PDF headings/layout are not inferred.
- DOCX paragraphs retain Heading-style sections; tables retain table and row numbers and
  searchable cell text in document order. Nested tables and complex layout are not preserved.
- Markdown ATX headings delimit sections; UTF-8 text/code is supported. Unsupported/binary
  formats, invalid UTF-8, and files without extractable text report errors. OCR is not included.
- Chunks retain document SHA-256, parser/chunker versions, and segment-relative offsets.
  Citation panels/exports show available pages/sections alongside chunk locations.

| Bound | Value |
|---|---|
| Uploaded file | 20 MiB |
| PDF pages / extracted characters | 500 / 2 million |
| DOCX expanded archive contents | 80 MiB |
| Chunks per document | 2,500; up to 1,200 characters each, 150-character overlap within a segment |
| Pending/queued/processing documents | 32 across the deployment |
| Embedding batch / maximum dimensions | 64 passages / 8,192 |
| Document processing time | Five minutes, checked between processing steps |
| Ready chunks in a full embedding rebuild | 10,000 |
| Vector components per document or full rebuild | 8 million |
| Authorized search candidate set | 10,000 chunks; narrow selected documents for larger libraries |

These are implementation bounds, not admin settings. Time checks are cooperative: an
individual parser/provider call can exceed the deadline and delay shutdown. This worker
is not a process-isolated parser sandbox. Large libraries need a future batched rebuild design.

SQL readiness, ownership, conversation scope, and assistant visibility remain authoritative
when serving results. Failed ingestion publication or reprocessing can leave stale Qdrant
points; they cannot become evidence and a successful full rebuild removes them. A hard
process crash can leave an unpublished staging collection, or an old collection whose
cleanup failed. Automatic collection garbage collection is not included. Operators should
preserve the collection named by the SQL `rag:index` setting when cleaning their own Qdrant
storage. Restoring SQL and running offline reindex reconstructs the derived index.
