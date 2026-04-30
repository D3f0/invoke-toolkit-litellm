# invoke-toolkit-litellm

A list of tasks to work with LiteLLM Proxy in different tools.

## Installation

### Use the plugin from `git`

```bash
uv tool install invoke-toolkit --with git+https://github.com/D3f0/invoke-toolkit-litellm
```

### Use the plugin from a checkout

Note that if you already run this step for other plugins, you may want
to add the `--with` or `--with-editable` of other plugins.

```bash
git clone https://github.com/D3f0/invoke-toolkit-litellm
cd invoke-toolkit-litellm
uv tool install invoke-toolkit --with-editable .
```

## Usage

Once installed, the tasks from this package will be automatically available in `invoke-toolkit`/`intk`:

```bash
intk -l
```

You should see a collection named `litellm` with the available tasks.

## Available Tasks

### `litellm.models` — List models

Lists the models available from the configured LiteLLM proxy.

```bash
intk litellm.models
```

### `litellm.test` — Test models

Verifies that inference works on the returned models by sending a small
request to each one concurrently.

```bash
# Test all models reported by the proxy
intk litellm.models | intk litellm.test

# Test a specific list
intk litellm.test --models '["my-model"]'
```

### `litellm.list-opencode` — List OpenCode providers and models

Lists the configured providers from `~/.config/opencode/opencode.json` and
the model ids currently present under each provider.

```bash
intk litellm.list-opencode
```

### `litellm.add-to-opencode` — Sync models into OpenCode

Reads `~/.config/opencode/opencode.json` (JSON with Comments), fetches the
current model list from one or more LiteLLM providers, and updates the
`models` map of each matching provider block — removing models that are no
longer active and adding new ones. If a provider id does not already exist,
it is created automatically. If the config file does not exist or is empty
it will be created automatically with the correct `$schema` reference.
New provider blocks include `baseURL` and `apiKey` in their `options`.
A timestamped backup is written next to the file before any change is made
(skipped for brand-new files).

```bash
intk litellm.add-to-opencode
```

Options:

| Flag | Description |
|------|-------------|
| `--url` | LiteLLM proxy URL for single-provider usage (or set `litellm.url` in config) |
| `--api-key` | Virtual API key for single-provider usage (or set `litellm.api_key` in config) |
| `--provider-id` | Provider key inside `opencode.json`; existing providers are updated and missing ones are created |
| `--provider` | Provider spec in the form `name:url:api-key`; may be passed multiple times |
| `--config-path` | Path to `opencode.json` (default: `~/.config/opencode/opencode.json`) |
| `--pick` / `-i` | Interactively select which models to include via fzf (or rich fallback) |

You can now upsert multiple providers in one run by repeating `--provider`.
The `name` part is matched against provider ids using fnmatch-style patterns,
so the same `url` can be reused with different API keys.

```bash
intk litellm.add-to-opencode \
  --provider 'team-a:https://litellm.example.com:KEY_A' \
  --provider 'team-b:https://litellm.example.com:KEY_B'
```

Pattern matching is also supported:

```bash
intk litellm.add-to-opencode \
  --provider 'team-*:https://litellm.example.com:SHARED_KEY'
```

When `--provider` is omitted, the command falls back to `--url` / `--api-key`
or the configured `litellm.url` and `litellm.api_key` values. When
`--provider-id` names a provider that is not yet present, the command creates
it and fills its `models` map from the fetched model list.

#### Interactive model selection

Pass `--pick` to open an fzf multi-select picker over the active model list.
Only the models you confirm will be written to the config.

```bash
intk litellm.add-to-opencode --pick
```

> **Note:** Install [fzf](https://github.com/junegunn/fzf) for full multi-select
> support.  Without it, a rich-based numbered selector is used as a fallback.

### `litellm.list-zed` — List Zed providers and models

Lists the configured `language_models.openai_compatible` providers from
`~/.config/zed/settings.json` and the model names currently present under
each provider.

```bash
intk litellm.list-zed
```

### `litellm.add-to-zed` — Sync models into Zed

Reads `~/.config/zed/settings.json` (JSON with Comments) and replaces the
`available_models` array of the matching
`language_models.openai_compatible` providers. Missing providers are created
automatically. Only those arrays are rewritten; all other content —
including comments — is preserved verbatim. A timestamped backup is written
next to the file before any change is made.

```bash
intk litellm.add-to-zed
```

Options:

| Flag | Description |
|------|-------------|
| `--url` | LiteLLM proxy URL for single-provider usage (or set `litellm.url` in config) |
| `--api-key` | Virtual API key for single-provider usage (or set `litellm.api_key` in config) |
| `--provider-id` | Provider key inside `language_models.openai_compatible`; existing providers are updated and missing ones are created |
| `--provider` | Provider spec in the form `name:url:api-key`; may be passed multiple times |
| `--config-path` | Path to `settings.json` (default: `~/.config/zed/settings.json`) |
| `--pick` / `-i` | Interactively select which models to include via fzf (or rich fallback) |

You can now upsert multiple Zed providers in one run by repeating
`--provider`. The `name` part is matched against configured provider ids
using fnmatch-style patterns, which makes it easy to target multiple
providers while still allowing the same `url` to be used more than once.

```bash
intk litellm.add-to-zed \
  --provider 'team-a:https://litellm.example.com:KEY_A' \
  --provider 'team-b:https://litellm.example.com:KEY_B'
```

When `--provider` is omitted, the command falls back to `--url` / `--api-key`
or the configured `litellm.url` and `litellm.api_key` values. When
`--provider-id` names a provider that is not yet present, the command creates
it with the provided LiteLLM connection details and generated model entries.

#### Interactive model selection

```bash
intk litellm.add-to-zed --pick
```

New models are added with sensible defaults (`max_tokens: 200000`, `tools: true`).
Existing model entries are preserved as-is.

## Configuration

### Storing keys in config

```bash
intk -x config.set --location user -p litellm.url -v https://my-litellm-proxy/
intk -x config.set --location user -p litellm.api_key -v <key here>
```

### Retrieving the key from 1Password

Requires the [`invoke-toolkit-1password`](https://github.com/D3f0/invoke-toolkit-1password) extension.

For multi-provider upserts, prefer resolving secrets at invocation time and
passing them through repeated `--provider name:url:api-key` arguments instead
of storing every key in config.

```bash
intk -x config.set --location user -p litellm.api_key -v $(intk 1password.password-by-url --url-part litellm)
```

## License

MIT