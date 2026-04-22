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

### `litellm.add-to-opencode` — Sync models into OpenCode

Reads `~/.config/opencode/opencode.json` (JSON with Comments), fetches the
current model list from the LiteLLM proxy, and updates the `models` map of
the matching provider block — removing models that are no longer active and
adding new ones.  A timestamped backup is written next to the file before
any change is made.

```bash
intk litellm.add-to-opencode
```

Options:

| Flag | Description |
|------|-------------|
| `--url` | LiteLLM proxy URL (or set `litellm.url` in config) |
| `--api-key` | Virtual API key (or set `litellm.api_key` in config) |
| `--provider-id` | Provider key inside `opencode.json` (auto-detected when only one exists) |
| `--config-path` | Path to `opencode.json` (default: `~/.config/opencode/opencode.json`) |
| `--pick` / `-i` | Interactively select which models to include via fzf (or rich fallback) |

#### Interactive model selection

Pass `--pick` to open an fzf multi-select picker over the active model list.
Only the models you confirm will be written to the config.

```bash
intk litellm.add-to-opencode --pick
```

> **Note:** Install [fzf](https://github.com/junegunn/fzf) for full multi-select
> support.  Without it, a rich-based numbered selector is used as a fallback.

### `litellm.add-to-zed` — Sync models into Zed

Reads `~/.config/zed/settings.json` (JSON with Comments) and replaces the
`available_models` array of the target `language_models.openai_compatible`
provider.  Only the array is rewritten; all other content — including
comments — is preserved verbatim.  A timestamped backup is written next to
the file before any change is made.

```bash
intk litellm.add-to-zed
```

Options:

| Flag | Description |
|------|-------------|
| `--url` | LiteLLM proxy URL (or set `litellm.url` in config) |
| `--api-key` | Virtual API key (or set `litellm.api_key` in config) |
| `--provider-id` | Key inside `language_models.openai_compatible` (auto-detected when only one exists) |
| `--config-path` | Path to `settings.json` (default: `~/.config/zed/settings.json`) |
| `--pick` / `-i` | Interactively select which models to include via fzf (or rich fallback) |

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

```bash
intk -x config.set --location user -p litellm.api_key -v $(intk 1password.password-by-url --url-part litellm)
```

## License

MIT