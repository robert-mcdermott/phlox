# Model discovery

Phlox can list the models available from a configured provider. Pull a model in Ollama
or download one in LM Studio, then open **Settings → Model → Choose model** to refresh
the choices. You can also use **Refresh models**. No Phlox restart is required.

The same searchable picker is available when editing assistants and choosing a provider's
default model. Type a name to filter, use arrow keys and Enter to select, or enter an exact
custom ID. Escape closes the list. Discovery never changes your selected model automatically.

## Enable discovery in the admin console

1. Open **Settings → Configuration → Provider profiles**.
2. Add a profile or edit an existing one. Enter its name, endpoint and credentials.
3. Set **Model discovery** to **Automatic**.
4. Choose the **Discovery API**. Detection recognizes Ollama on port 11434 and LM Studio
   on port 1234; other endpoints use the OpenAI-compatible API. Select Ollama or LM Studio
   explicitly when using a custom port or reverse proxy.
5. Open **Default model** or click its refresh button. This reads the current form,
   including unsaved credentials; it does not save the profile. A blank secret reuses
   the saved credential for the same profile name.
6. Choose a default model, then **Save profiles**. New chats can use the profile immediately.

The generation endpoint still uses the OpenAI-compatible `/v1` base URL for Ollama and
LM Studio. Discovery removes the final `/v1` before requesting their native catalog,
preserving a proxy path prefix. The endpoint must be reachable from the **Phlox backend**;
inside a container, `localhost` refers to the container. See [Docker networking](DOCKER.md).

Discovery only requests catalogs: it never generates text, downloads or loads a model,
or tests tools. **Test generation** in the admin form and **Test connection** in Model
settings are separate actions that send a small prompt and may incur model charges.

## Automatic and curated lists

- **Automatic:** combine the provider catalog with the default model and any **Additional
  model IDs** entered by an administrator. Those extra IDs support private deployments or
  models omitted by the provider. Their availability remains unverified until used.
- **Curated list:** use only the default model and configured model IDs. No discovery
  requests are made to the provider.

Existing profiles with a nonempty `models` list remain curated unless an administrator
explicitly enables Automatic. Profiles without such a list default to Automatic; new
profiles added through the console also default to Automatic. No database migration is needed.

A curated list controls the picker, not authorization to invoke a model. Users can enter
custom IDs; provider permissions and Phlox's existing execution policies still apply.

For deployments seeded from a file, the equivalent optional fields are:

```yaml
profiles:
  local:
    type: openai
    endpoint: http://localhost:11434/v1
    api_key: ollama
    model: YOUR_INSTALLED_MODEL_ID
    model_discovery: automatic  # automatic or manual
    discovery_api: ollama       # auto, openai, ollama, lmstudio
    models: []                 # supplements Automatic; controls Curated list
    supports_tools: true
```

Admin saves use the existing database configuration overlay and take precedence over
the file seed. Normal setup and later changes can be done entirely in the console.

## Provider support and model details

| Provider | Catalog | Details shown when supplied |
|---|---|---|
| OpenAI-compatible | `GET /models` relative to the configured base URL | Model IDs; capability support remains unknown |
| Ollama | `GET /api/tags` | Installed IDs, parameter size, quantization and disk size |
| LM Studio | `GET /api/v1/models` | Downloaded IDs, display names, size, quantization, loaded state, tool/vision support and maximum context |
| AWS Bedrock | Foundation-model and inference-profile list APIs | Active inference profiles and non-legacy on-demand text foundation models in the configured region |

Native Ollama/LM Studio catalogs fall back to the configured OpenAI-compatible `/models`
endpoint when the native route returns 404, 405 or 501. Authentication failures are reported
without that fallback. Older LM Studio versions may therefore show fewer details or only
models visible through their compatible API.

Models explicitly identified as embeddings are excluded from chat choices. Generic IDs
and Ollama's tags do not establish a model's modality or tool support; Phlox does not guess
from names. Use a curated list if the provider mixes unsuitable models into its catalog.
Reported capabilities are informational: discovery does not override `supports_tools`,
context limits, tool permissions or generation settings. LM Studio's advertised maximum
context can exceed the context configured for a loaded instance.

Downloaded LM Studio models can appear before loading. Enable Just-In-Time loading in
LM Studio or load the selected model there before chatting. Ollama must finish pulling a
model before it appears in its installed catalog.

Bedrock discovery requires AWS credentials with `bedrock:ListFoundationModels` and
`bedrock:ListInferenceProfiles` permissions. A listed ID does not guarantee invocation
access or compatibility with Phlox's Converse adapter. Bedrock bearer-key profiles use a
curated list in this implementation; discovery explains this instead of invoking a model
to probe access. Explicit IDs remain useful for application or cross-region profiles.

## Refresh, failures and privacy

Opening the picker requests a refresh; ordinary catalog reads cache results for 60 seconds.
Refreshes within three seconds share a result to avoid duplicate requests. Successful
refreshes replace the discovered list, while the current selection remains available even
if it disappears from the catalog. A provider failure preserves the last successful list
and shows an error. If none is cached, configured IDs and your selection remain usable.

The cache is bounded, held in backend process memory, and separated by profile configuration
and credentials. Restarting the backend clears it. A changed endpoint or credential does
not inherit an old provider's cached list. Phlox's supported deployment remains one
application process per database/data directory.
Requests have connection/read timeouts and concurrency limits; HTTP catalogs are capped at
2 MiB and 2,000 entries. Bedrock lists at most four inference-profile pages. A truncated
catalog is labelled; enter an exact ID if needed.

Check the provider server, endpoint and credentials after connection errors; check listing
permissions for 401/403, and wait before retrying 429. Requests do not follow redirects,
forward credentials to another destination, or use environment proxy variables. Configure
the final endpoint directly. HTTP endpoints should honor `Accept-Encoding: identity`.
Provider errors are summarized without returning response bodies or credentials.

The browser receives model metadata, never saved provider secrets. Catalog listing requires
a signed-in user when authentication is enabled; discovering an unsaved endpoint requires
an administrator. The OpenAI-compatible **Phlox API gateway's `/v1/models` remains its
configured catalog**; it does not expose this dynamic UI catalog. See [API gateway](API_GATEWAY.md).

## Manual verification

1. Enable Automatic for an Ollama or LM Studio profile and save it. Open Model settings
   and select an existing chat model.
2. Pull/download another model outside Phlox. Reopen the picker after at least three
   seconds; search for the new ID and select it. Send a short chat to verify generation.
3. Stop the model server and refresh. Confirm an error appears with the previous list and
   selection preserved. Restart the server and refresh to recover.
4. Switch the profile to Curated, save, and verify only configured choices appear. Return
   to Automatic when finished.
5. Add a new profile in the console and discover its default before saving. Confirm this
   alone does not add the profile to the user provider selector. Save to make it available.

Provider references: [Ollama tags](https://docs.ollama.com/api/tags),
[LM Studio catalog](https://lmstudio.ai/docs/developer/rest/list),
[Bedrock foundation models](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_ListFoundationModels.html),
and [Bedrock inference profiles](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_ListInferenceProfiles.html).
