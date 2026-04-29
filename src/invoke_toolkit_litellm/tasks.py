"""LiteLLM tasks"""

import json
import pathlib
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from fnmatch import fnmatch
from textwrap import dedent
from typing import Annotated

import attrs
import httpx
import jsonclark
from invoke.util import debug
from invoke_toolkit import Context, task
from invoke_toolkit.config import get_config_value
from invoke_toolkit.utils.fzf import select as fzf_select

OPENCODE_CONFIG_PATH = pathlib.Path("~/.config/opencode/opencode.json").expanduser()
ZED_CONFIG_PATH = pathlib.Path("~/.config/zed/settings.json").expanduser()

# Default capabilities applied to every new model entry written to Zed settings.
_ZED_DEFAULT_CAPABILITIES: dict = {
    "tools": True,
    "images": False,
    "parallel_tool_calls": False,
    "prompt_cache_key": False,
}
_ZED_DEFAULT_MAX_TOKENS = 200_000


@attrs.define(frozen=True)
class ProviderSpec:
    name: str
    url: str | None = None
    api_key: str | None = None

    @classmethod
    def parse(cls, raw: str) -> "ProviderSpec":
        try:
            name, remainder = raw.split(":", 1)
        except ValueError as exc:
            raise ValueError("Provider must use the format name:url[:api-key]") from exc
        if not name:
            raise ValueError("Provider name cannot be empty")

        if ":" in remainder:
            url, api_key = remainder.rsplit(":", 1)
            if not url:
                raise ValueError("Provider url cannot be empty")
            if not api_key:
                raise ValueError("Provider api-key cannot be empty")
            return cls(name=name, url=url.removesuffix("/"), api_key=api_key)

        url = remainder
        if not url:
            raise ValueError("Provider url cannot be empty")
        return cls(name=name, url=url.removesuffix("/"), api_key=None)


@attrs.define(frozen=True)
class ProviderSync:
    provider_id: str
    spec: ProviderSpec
    exists: bool = True


def _get_default_provider_spec(ctx: Context) -> ProviderSpec:
    return ProviderSpec(
        name="*",
        url=get_config_value(
            ctx,
            "litellm.url",
            required=True,
            exit_message=(
                "Please provide --provider, --url/--api-key, or set the config "
                "litellm.url"
            ),
        ).removesuffix("/"),
        api_key=get_config_value(
            ctx,
            "litellm.api_key",
            required=True,
            exit_message=(
                "Please provide --provider, --url/--api-key, or set the config "
                "litellm.api_key"
            ),
        ),
    )


def _provider_specs_from_args(
    ctx: Context,
    provider: list[str],
    provider_id: str,
    url: str,
    api_key: str,
) -> list[ProviderSpec]:
    specs = [ProviderSpec.parse(raw) for raw in provider]

    if provider_id:
        if provider and (url or api_key):
            ctx.rich_exit(
                "Do not combine [bold]--provider-id[/] with [bold]--provider[/] "
                "and [bold]--url[/]/[bold]--api-key[/] in the same invocation."
            )

        if not provider and bool(url) != bool(api_key):
            ctx.rich_exit(
                "Please provide both [bold]--url[/] and [bold]--api-key[/] "
                "when using [bold]--provider-id[/]."
            )

        legacy_spec = (
            ProviderSpec(name=provider_id, url=url.removesuffix("/"), api_key=api_key)
            if url and api_key
            else ProviderSpec(name=provider_id)
        )
        specs.append(legacy_spec)

    if not specs:
        if bool(url) != bool(api_key):
            ctx.rich_exit(
                "Please provide both [bold]--url[/] and [bold]--api-key[/], "
                "or use [bold]--provider[/]."
            )
        if url and api_key:
            specs = [ProviderSpec(name="*", url=url.removesuffix("/"), api_key=api_key)]
        else:
            specs = [_get_default_provider_spec(ctx)]

    return specs


def _resolve_provider_syncs(
    ctx: Context,
    specs: list[ProviderSpec],
    available_provider_ids: list[str],
    providers: dict | None = None,
) -> list[ProviderSync]:
    syncs: list[ProviderSync] = []
    matched_provider_ids: set[str] = set()

    for spec in specs:
        current_matches = [
            provider_id
            for provider_id in available_provider_ids
            if fnmatch(provider_id, spec.name)
        ]
        if current_matches:
            for provider_id in current_matches:
                if provider_id in matched_provider_ids:
                    ctx.rich_exit(
                        "Provider "
                        f"[bold]{provider_id}[/] matched more than one provider spec. "
                        "Please make the patterns disjoint."
                    )
                matched_provider_ids.add(provider_id)
                resolved_spec = _resolve_spec_from_existing(ctx, spec, provider_id, providers or {})
                syncs.append(
                    ProviderSync(provider_id=provider_id, spec=resolved_spec, exists=True)
                )
            continue

        if any(char in spec.name for char in "*?["):
            ctx.rich_exit(
                "Provider pattern "
                f"[bold]{spec.name}[/] did not match any configured provider. "
                f"Available providers: {available_provider_ids}"
            )

        if spec.name in matched_provider_ids:
            ctx.rich_exit(
                "Provider "
                f"[bold]{spec.name}[/] matched more than one provider spec. "
                "Please make the patterns disjoint."
            )
        matched_provider_ids.add(spec.name)
        syncs.append(ProviderSync(provider_id=spec.name, spec=spec, exists=False))

    return syncs


def _resolve_spec_from_existing(
    ctx: Context,
    spec: ProviderSpec,
    provider_id: str,
    providers: dict,
) -> ProviderSpec:
    """Resolve missing url/api_key on *spec* from the existing provider config.

    Returns a new ProviderSpec with both fields populated, or exits with a
    descriptive error if a required value cannot be found.
    """
    if spec.url is not None and spec.api_key is not None:
        return spec

    existing_options: dict = providers.get(provider_id, {}).get("options", {})
    resolved_url = spec.url if spec.url is not None else existing_options.get("baseURL")
    resolved_key = spec.api_key if spec.api_key is not None else existing_options.get("apiKey")

    if resolved_url is None:
        ctx.rich_exit(
            f"Provider [bold]{provider_id}[/] is missing a URL. "
            "Please provide [bold]--url[/], use [bold]--provider name:url:api-key[/], "
            f"or set [bold]provider.{provider_id}.options.baseURL[/] in opencode.json."
        )
    if resolved_key is None:
        ctx.rich_exit(
            f"Provider [bold]{provider_id}[/] is missing an API key. "
            "Please provide [bold]--api-key[/], use [bold]--provider name:url:api-key[/], "
            f"or set [bold]provider.{provider_id}.options.apiKey[/] in opencode.json."
        )

    return attrs.evolve(spec, url=resolved_url, api_key=resolved_key)


def _fetch_active_models(ctx: Context, spec: ProviderSpec) -> list[str]:
    with ctx.status(f"Fetching models from {spec.url}"):
        resp = httpx.get(
            url=f"{spec.url}/models",
            headers={"x-litellm-api-key": spec.api_key},
        )
        resp.raise_for_status()
    return [m["id"] for m in resp.json()["data"]]


def _pick_active_models(
    ctx: Context,
    active_models: list[str],
    *,
    prompt: str,
) -> list[str]:
    chosen = fzf_select(
        ctx,
        active_models,
        prompt=prompt,
        multi=True,
    )
    if not chosen:
        ctx.rich_exit("[yellow]No models selected, nothing to do.[/]")
    return list(chosen)


def _get_opencode_config(config_path: str) -> tuple[pathlib.Path, dict]:
    cfg_path = (
        pathlib.Path(config_path).expanduser() if config_path else OPENCODE_CONFIG_PATH
    )
    if not cfg_path.exists():
        raise FileNotFoundError(
            dedent(f"""\
                OpenCode config not found at {cfg_path}.
                Please create it first or pass --config-path.
            """)
        )
    raw_text = cfg_path.read_text(encoding="utf-8")
    return cfg_path, jsonclark.loads(raw_text)


def _get_zed_config(config_path: str) -> tuple[pathlib.Path, str, dict]:
    cfg_path = (
        pathlib.Path(config_path).expanduser() if config_path else ZED_CONFIG_PATH
    )
    if not cfg_path.exists():
        raise FileNotFoundError(
            dedent(f"""\
                Zed settings not found at {cfg_path}.
                Please create it first or pass --config-path.
            """)
        )
    raw_text = cfg_path.read_text(encoding="utf-8")
    return cfg_path, raw_text, jsonclark.loads(raw_text)


@task(autoprint=True, aliases=["models", "m"])
def get_models(
    ctx: Context,
    url: Annotated[str, "The LiteLLM proxy url"] = "",
    api_key: Annotated[str, "The Virtual API Key"] = "",
):
    """
    Lists the models available for an URL and an API key
    """
    spec = (
        ProviderSpec(name="*", url=url.removesuffix("/"), api_key=api_key)
        if url and api_key
        else _get_default_provider_spec(ctx)
    )
    models = _fetch_active_models(ctx, spec)
    return json.dumps(models)


@task(aliases=["test", "t"], autoprint=True)
def test_models(
    ctx: Context,
    models: Annotated[
        list[str], "Models to check, otherwise expected as JSON from [red]stdin[/]"
    ] = [],
    url: Annotated[str, "The LiteLLM proxy url"] = "",
    api_key: Annotated[str, "The Virtual API Key"] = "",
    no_progress: Annotated[bool, "Don't show progress"] = False,
):
    """Verify that inference works on the returned models."""
    url = url or get_config_value(
        ctx,
        "litellm.url",
        required=True,
        exit_message="Please provide --url or set the config litellm.url",
    )
    api_key = api_key or get_config_value(
        ctx,
        "litellm.api_key",
        required=True,
        exit_message="Please provide --api-key or set the config litellm.api_key",
    )
    url = url.removesuffix("/")
    if not models:
        ctx.print_err(
            "No [red]--models[/] passed, reading JSON list from stdin, "
            "alternatively use intk litellm.models | intk litellm.test"
        )
        try:
            models = json.load(sys.stdin)
        except Exception as e:
            ctx.rich_exit(f"Error while reading a list of models from stdin: {e}")

    def test_model(model: str, retries: int = 3) -> tuple[str, httpx.Response]:
        for attempt in range(1, retries + 1):
            try:
                resp = httpx.post(
                    url=f"{url}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "Hello!"}],
                    },
                )
                return model, resp
            except httpx.TimeoutException as e:
                debug(f"Retrying model {model} because of {e}")
                if attempt == retries:
                    raise
        raise RuntimeError("unreachable")

    results: dict[str, bool] = {}
    with ThreadPoolExecutor() as pool:
        futures = {pool.submit(test_model, model): model for model in models}
        for future in as_completed(futures):
            model, resp = future.result()
            results[model] = resp.is_success
            if not no_progress:
                if resp.is_success:
                    ctx.print_err(f"[green]✓[/] {model}")
                else:
                    ctx.print_err(
                        f"[red]✗[/] {model} — {resp.status_code}: {resp.text}"
                    )

    return json.dumps(results)


@task(autoprint=True)
def list_opencode(
    ctx: Context,
    config_path: Annotated[
        str, "Path to opencode.json (default: ~/.config/opencode/opencode.json)"
    ] = "",
):
    """List configured OpenCode providers and their models."""
    try:
        _, config = _get_opencode_config(config_path)
    except FileNotFoundError as exc:
        ctx.rich_exit(str(exc))

    providers: dict = config.get("provider", {})
    if not providers:
        ctx.rich_exit(
            "No [bold]provider[/] key found in opencode.json. Nothing to list."
        )

    result = {
        provider_id: sorted(provider_cfg.get("models", {}).keys())
        for provider_id, provider_cfg in providers.items()
    }
    return json.dumps(result)


@task()
def add_to_opencode(
    ctx: Context,
    url: Annotated[str, "The LiteLLM proxy url"] = "",
    api_key: Annotated[str, "The Virtual API Key"] = "",
    provider_id: Annotated[str, "The provider id in opencode.json to update"] = "",
    provider: Annotated[
        list[str],
        "Provider spec in the form name:url:api-key. Name supports fnmatch.",
    ] = [],
    config_path: Annotated[
        str, "Path to opencode.json (default: ~/.config/opencode/opencode.json)"
    ] = "",
    pick: Annotated[
        bool, "Interactively pick which models to include (fzf or fallback)"
    ] = False,
):
    """Sync active LiteLLM models into the OpenCode configuration file.

    Reads ~/.config/opencode/opencode.json (JSON with Comments), fetches the
    current model list from one or more LiteLLM providers, removes models that
    are no longer active from the matching provider blocks, and adds any new ones.
    """
    provider_specs = _provider_specs_from_args(ctx, provider, provider_id, url, api_key)

    try:
        cfg_path, config = _get_opencode_config(config_path)
    except FileNotFoundError as exc:
        ctx.rich_exit(str(exc))

    providers: dict = config.get("provider", {})
    if not providers:
        ctx.rich_exit(
            "No [bold]provider[/] key found in opencode.json. Nothing to update."
        )

    provider_syncs = _resolve_provider_syncs(
        ctx, provider_specs, list(providers.keys()), providers
    )
    changes: list[tuple[str, set[str], set[str], int]] = []

    for sync in provider_syncs:
        active_models = _fetch_active_models(ctx, sync.spec)

        if pick:
            active_models = _pick_active_models(
                ctx,
                active_models,
                prompt=f"Select models to add to OpenCode for {sync.provider_id}",
            )

        provider_cfg: dict = providers.get(sync.provider_id, {})
        existing_models: dict = provider_cfg.get("models", {})

        active_set = set(active_models)
        existing_set = set(existing_models.keys())

        removed = existing_set - active_set
        added = active_set - existing_set

        updated_models: dict = {}
        for model_id in active_models:
            if model_id in existing_models:
                updated_models[model_id] = existing_models[model_id]
            else:
                updated_models[model_id] = {"name": model_id}

        if not sync.exists:
            provider_cfg = {
                "models": updated_models,
                "options": {},
            }
            providers[sync.provider_id] = provider_cfg
        else:
            provider_cfg["models"] = updated_models
        changes.append((sync.provider_id, removed, added, len(updated_models)))

    backup_path = _backup(cfg_path)
    ctx.print_err(f"[dim]Backup written to {backup_path}[/]")

    cfg_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    for matched_provider_id, removed, added, total_models in changes:
        if removed:
            ctx.print_err(
                f"[yellow]Removed {len(removed)} model(s) from {matched_provider_id}:[/] "
                f"{sorted(removed)}"
            )
        if added:
            ctx.print_err(
                f"[green]Added {len(added)} model(s) to {matched_provider_id}:[/] "
                f"{sorted(added)}"
            )
        if not removed and not added:
            ctx.print_err(
                f"[dim]Provider {matched_provider_id} models already up to date.[/]"
            )
        else:
            ctx.print_err(
                f"[bold green]✓[/] Updated [bold]{matched_provider_id}[/] in {cfg_path} "
                f"({total_models} active model(s))"
            )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _backup(cfg_path: pathlib.Path) -> pathlib.Path:
    """Copy *cfg_path* to a timestamped backup next to the original and return
    the backup path.  Example: ``settings.json`` → ``settings.json.2025-07-01T143000``
    """
    timestamp = datetime.now().strftime("%Y-%m-%dT%H%M%S")
    backup_path = cfg_path.with_name(f"{cfg_path.name}.{timestamp}")
    shutil.copy2(cfg_path, backup_path)
    return backup_path


# ---------------------------------------------------------------------------
# Helpers for add_to_zed
# ---------------------------------------------------------------------------


def _find_bracket_span(
    text: str, start: int, open_ch: str, close_ch: str
) -> tuple[int, int]:
    """Return the half-open ``(start, end)`` span of a bracketed region in
    *text* beginning at *start*, which must point at *open_ch*.

    Correctly skips brackets that appear inside quoted strings and handles
    ``\\``-escaped characters within strings.
    """
    assert text[start] == open_ch, (
        f"Expected {open_ch!r} at position {start}, got {text[start]!r}"
    )
    depth = 0
    i = start
    in_string = False
    escape_next = False
    while i < len(text):
        ch = text[i]
        if escape_next:
            escape_next = False
        elif ch == "\\" and in_string:
            escape_next = True
        elif ch == '"':
            in_string = not in_string
        elif not in_string:
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return start, i + 1
        i += 1
    raise ValueError(f"Unmatched {open_ch!r} at position {start}")


def _locate_available_models_span(text: str, provider_id: str) -> tuple[int, int]:
    """Find the character span of the ``available_models`` array for
    *provider_id* inside ``language_models.openai_compatible``.

    The search is text-based so surrounding comments are left untouched.

    Returns ``(array_start, array_end)`` as a half-open range.
    Raises ``KeyError`` if the provider or ``available_models`` key is not found.
    """
    provider_pattern = re.compile(r'"' + re.escape(provider_id) + r'"\s*:\s*\{')
    m = provider_pattern.search(text)
    if not m:
        raise KeyError(f"Provider {provider_id!r} not found in settings file")

    search_from = m.end()
    avail_pattern = re.compile(r'"available_models"\s*:\s*\[')
    am = avail_pattern.search(text, search_from)
    if not am:
        raise KeyError(f"'available_models' key not found for provider {provider_id!r}")

    array_start = am.end() - 1  # points at the '['
    return _find_bracket_span(text, array_start, "[", "]")


def _build_zed_model_entry(model_id: str, existing_entry: dict | None) -> dict:
    """Return a Zed model entry dict for *model_id*.

    Existing entries are preserved verbatim; new ones receive sensible defaults.
    """
    if existing_entry is not None:
        return existing_entry
    return {
        "name": model_id,
        "display_name": model_id,
        "max_tokens": _ZED_DEFAULT_MAX_TOKENS,
        "capabilities": dict(_ZED_DEFAULT_CAPABILITIES),
    }


def _render_zed_models_array(models: list[dict], indent: int) -> str:
    """Render *models* as a JSON array indented to match its position in the
    Zed settings file.

    *indent* is the number of spaces used for the ``available_models`` key
    itself; each model object is indented by *indent* + 2.
    """
    item_indent = " " * (indent + 2)
    close_indent = " " * indent
    lines: list[str] = ["["]
    for i, model in enumerate(models):
        trailing_comma = "," if i < len(models) - 1 else ""
        serialized = json.dumps(model, indent=2, ensure_ascii=False)
        # Re-indent every line of the serialized object
        obj_lines = serialized.splitlines()
        reindented = [item_indent + obj_lines[0]]
        for ln in obj_lines[1:]:
            reindented.append(item_indent + ln)
        lines.append("\n".join(reindented) + trailing_comma)
    lines.append(close_indent + "]")
    return "\n".join(lines)


def _detect_indent(text: str, pos: int) -> int:
    """Return the number of leading spaces on the line containing *pos*."""
    line_start = text.rfind("\n", 0, pos) + 1
    count = 0
    while line_start + count < len(text) and text[line_start + count] == " ":
        count += 1
    return count


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@task(autoprint=True)
def list_zed(
    ctx: Context,
    config_path: Annotated[
        str, "Path to Zed settings.json (default: ~/.config/zed/settings.json)"
    ] = "",
):
    """List configured Zed openai_compatible providers and their models."""
    try:
        _, _, config = _get_zed_config(config_path)
    except FileNotFoundError as exc:
        ctx.rich_exit(str(exc))

    oai_compat: dict = config.get("language_models", {}).get("openai_compatible", {})
    if not oai_compat:
        ctx.rich_exit(
            "No [bold]language_models.openai_compatible[/] key found in "
            "Zed settings. Nothing to list."
        )

    result = {
        provider_id: sorted(
            entry["name"]
            for entry in provider_cfg.get("available_models", [])
            if "name" in entry
        )
        for provider_id, provider_cfg in oai_compat.items()
    }
    return json.dumps(result)


@task()
def add_to_zed(
    ctx: Context,
    url: Annotated[str, "The LiteLLM proxy url"] = "",
    api_key: Annotated[str, "The Virtual API Key"] = "",
    provider_id: Annotated[
        str, "The openai_compatible provider id in Zed settings to update"
    ] = "",
    provider: Annotated[
        list[str],
        "Provider spec in the form name:url:api-key. Name supports fnmatch.",
    ] = [],
    config_path: Annotated[
        str, "Path to Zed settings.json (default: ~/.config/zed/settings.json)"
    ] = "",
    pick: Annotated[
        bool, "Interactively pick which models to include (fzf or fallback)"
    ] = False,
):
    """Sync active LiteLLM models into the Zed editor settings file.

    Reads ~/.config/zed/settings.json (JSON with Comments), fetches the current
    model list from one or more LiteLLM providers, then replaces the
    ``available_models`` array of the matching
    ``language_models.openai_compatible`` providers — removing models that are
    no longer active and adding new ones with default capabilities. All other
    content (including comments) is preserved verbatim.
    """
    provider_specs = _provider_specs_from_args(ctx, provider, provider_id, url, api_key)

    try:
        cfg_path, raw_text, config = _get_zed_config(config_path)
    except FileNotFoundError as exc:
        ctx.rich_exit(str(exc))

    oai_compat: dict = config.get("language_models", {}).get("openai_compatible", {})
    if not oai_compat:
        ctx.rich_exit(
            "No [bold]language_models.openai_compatible[/] key found in "
            "Zed settings. Nothing to update."
        )

    provider_syncs = _resolve_provider_syncs(
        ctx, provider_specs, list(oai_compat.keys())
    )
    rendered_arrays: list[tuple[int, int, str]] = []
    changes: list[tuple[str, set[str], set[str], int]] = []

    for sync in provider_syncs:
        active_models = _fetch_active_models(ctx, sync.spec)

        if pick:
            active_models = _pick_active_models(
                ctx,
                active_models,
                prompt=f"Select models to add to Zed for {sync.provider_id}",
            )

        existing_provider_cfg: dict = oai_compat.get(sync.provider_id, {})
        existing_entries: list[dict] = existing_provider_cfg.get("available_models", [])
        existing_by_name: dict[str, dict] = {e["name"]: e for e in existing_entries}

        active_set = set(active_models)
        existing_set = set(existing_by_name.keys())

        removed = existing_set - active_set
        added = active_set - existing_set

        updated_entries = [
            _build_zed_model_entry(mid, existing_by_name.get(mid))
            for mid in active_models
        ]

        if sync.exists:
            try:
                arr_start, arr_end = _locate_available_models_span(
                    raw_text, sync.provider_id
                )
            except KeyError as exc:
                ctx.rich_exit(f"[red]Error:[/] {exc}")

            indent_col = _detect_indent(raw_text, arr_start)
            new_array = _render_zed_models_array(updated_entries, indent_col)
            rendered_arrays.append((arr_start, arr_end, new_array))
        else:
            oai_compat[sync.provider_id] = {
                "name": sync.provider_id,
                "api_url": sync.spec.url,
                "api_key": sync.spec.api_key,
                "available_models": updated_entries,
            }

        changes.append((sync.provider_id, removed, added, len(updated_entries)))

    if rendered_arrays:
        new_text = raw_text
        for arr_start, arr_end, new_array in sorted(rendered_arrays, reverse=True):
            new_text = new_text[:arr_start] + new_array + new_text[arr_end:]
    else:
        new_text = json.dumps(config, indent=2, ensure_ascii=False) + "\n"

    backup_path = _backup(cfg_path)
    ctx.print_err(f"[dim]Backup written to {backup_path}[/]")

    cfg_path.write_text(new_text, encoding="utf-8")

    for matched_provider_id, removed, added, total_models in changes:
        if removed:
            ctx.print_err(
                f"[yellow]Removed {len(removed)} model(s) from {matched_provider_id}:[/] "
                f"{sorted(removed)}"
            )
        if added:
            ctx.print_err(
                f"[green]Added {len(added)} model(s) to {matched_provider_id}:[/] "
                f"{sorted(added)}"
            )
        if not removed and not added:
            ctx.print_err(
                f"[dim]Provider {matched_provider_id} models already up to date.[/]"
            )
        else:
            ctx.print_err(
                f"[bold green]✓[/] Updated [bold]{matched_provider_id}[/] in {cfg_path} "
                f"({total_models} active model(s))"
            )
