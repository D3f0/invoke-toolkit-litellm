"""LiteLLM tasks"""

import json
import pathlib
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from textwrap import dedent
from typing import Annotated

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


@task(autoprint=True, aliases=["models", "m"])
def get_models(
    ctx: Context,
    url: Annotated[str, "The LiteLLM proxy url"] = "",
    api_key: Annotated[str, "The Virtual API Key"] = "",
):
    """
    Lists the models available for an URL and an API key
    """
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
    models_url: str = f"{url}/models"
    with ctx.status(f"Querying models in {url}"):
        resp = httpx.get(url=models_url, headers={"x-litellm-api-key": api_key})
        resp.raise_for_status()
    response = resp.json()
    models = [m["id"] for m in response["data"]]
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


@task()
def add_to_opencode(
    ctx: Context,
    url: Annotated[str, "The LiteLLM proxy url"] = "",
    api_key: Annotated[str, "The Virtual API Key"] = "",
    provider_id: Annotated[str, "The provider id in opencode.json to update"] = "",
    config_path: Annotated[
        str, "Path to opencode.json (default: ~/.config/opencode/opencode.json)"
    ] = "",
    pick: Annotated[
        bool, "Interactively pick which models to include (fzf or fallback)"
    ] = False,
):
    """Sync active LiteLLM models into the OpenCode configuration file.

    Reads ~/.config/opencode/opencode.json (JSON with Comments), fetches the
    current model list from the LiteLLM proxy, removes models that are no longer
    active from the given provider block, and adds any new ones.
    """
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

    # Fetch active models from LiteLLM proxy
    with ctx.status(f"Fetching models from {url}"):
        resp = httpx.get(
            url=f"{url}/models",
            headers={"x-litellm-api-key": api_key},
        )
        resp.raise_for_status()
    active_models: list[str] = [m["id"] for m in resp.json()["data"]]

    if pick:
        chosen = fzf_select(
            ctx,
            active_models,
            prompt="Select models to add to OpenCode",
            multi=True,
        )
        if not chosen:
            ctx.rich_exit("[yellow]No models selected, nothing to do.[/]")
        active_models = list(chosen)

    # Resolve config file path
    cfg_path = (
        pathlib.Path(config_path).expanduser() if config_path else OPENCODE_CONFIG_PATH
    )

    if not cfg_path.exists():
        ctx.rich_exit(
            dedent(f"""\
                OpenCode config not found at [bold]{cfg_path}[/].
                Please create it first or pass [bold]--config-path[/].
            """)
        )

    # Parse JSONC file
    raw_text = cfg_path.read_text(encoding="utf-8")
    config: dict = jsonclark.loads(raw_text)

    providers: dict = config.get("provider", {})
    if not providers:
        ctx.rich_exit(
            "No [bold]provider[/] key found in opencode.json. Nothing to update."
        )

    # Resolve which provider to update
    if not provider_id:
        if len(providers) == 1:
            provider_id = next(iter(providers))
        else:
            provider_ids = list(providers.keys())
            ctx.print_err(
                dedent(f"""\
                    Multiple providers found: {provider_ids}
                    Please pass [bold]--provider-id[/] to choose one.
                """)
            )
            ctx.rich_exit("")

    if provider_id not in providers:
        ctx.rich_exit(
            f"Provider [bold]{provider_id}[/] not found in opencode.json. "
            f"Available providers: {list(providers.keys())}"
        )

    provider_cfg: dict = providers[provider_id]
    existing_models: dict = provider_cfg.get("models", {})

    active_set = set(active_models)
    existing_set = set(existing_models.keys())

    removed = existing_set - active_set
    added = active_set - existing_set

    # Build updated models dict: keep only active models, preserving existing metadata
    updated_models: dict = {}
    for model_id in active_models:
        if model_id in existing_models:
            updated_models[model_id] = existing_models[model_id]
        else:
            updated_models[model_id] = {"name": model_id}

    provider_cfg["models"] = updated_models

    # Back up the original file before overwriting
    backup_path = _backup(cfg_path)
    ctx.print_err(f"[dim]Backup written to {backup_path}[/]")

    # Write back as clean JSON (2-space indent)
    cfg_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Report changes
    if removed:
        ctx.print_err(f"[yellow]Removed {len(removed)} model(s):[/] {sorted(removed)}")
    if added:
        ctx.print_err(f"[green]Added {len(added)} model(s):[/] {sorted(added)}")
    if not removed and not added:
        ctx.print_err("[dim]Models already up to date.[/]")
    else:
        ctx.print_err(
            f"[bold green]✓[/] Updated [bold]{provider_id}[/] in {cfg_path} "
            f"({len(updated_models)} active model(s))"
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


@task()
def add_to_zed(
    ctx: Context,
    url: Annotated[str, "The LiteLLM proxy url"] = "",
    api_key: Annotated[str, "The Virtual API Key"] = "",
    provider_id: Annotated[
        str, "The openai_compatible provider id in Zed settings to update"
    ] = "",
    config_path: Annotated[
        str, "Path to Zed settings.json (default: ~/.config/zed/settings.json)"
    ] = "",
    pick: Annotated[
        bool, "Interactively pick which models to include (fzf or fallback)"
    ] = False,
):
    """Sync active LiteLLM models into the Zed editor settings file.

    Reads ~/.config/zed/settings.json (JSON with Comments), fetches the current
    model list from the LiteLLM proxy, then replaces the ``available_models``
    array of the given ``language_models.openai_compatible`` provider — removing
    models that are no longer active and adding new ones with default capabilities.
    All other content (including comments) is preserved verbatim.
    """
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

    # Fetch active models from LiteLLM proxy
    with ctx.status(f"Fetching models from {url}"):
        resp = httpx.get(
            url=f"{url}/models",
            headers={"x-litellm-api-key": api_key},
        )
        resp.raise_for_status()
    active_models: list[str] = [m["id"] for m in resp.json()["data"]]

    if pick:
        chosen = fzf_select(
            ctx,
            active_models,
            prompt="Select models to add to Zed",
            multi=True,
        )
        if not chosen:
            ctx.rich_exit("[yellow]No models selected, nothing to do.[/]")
        active_models = list(chosen)

    # Resolve config file path
    cfg_path = (
        pathlib.Path(config_path).expanduser() if config_path else ZED_CONFIG_PATH
    )

    if not cfg_path.exists():
        ctx.rich_exit(
            dedent(f"""\
                Zed settings not found at [bold]{cfg_path}[/].
                Please create it first or pass [bold]--config-path[/].
            """)
        )

    raw_text = cfg_path.read_text(encoding="utf-8")

    # Parse to discover existing providers and models (comments are discarded
    # from the parsed object but kept in raw_text for the splice below)
    config: dict = jsonclark.loads(raw_text)

    oai_compat: dict = config.get("language_models", {}).get("openai_compatible", {})
    if not oai_compat:
        ctx.rich_exit(
            "No [bold]language_models.openai_compatible[/] key found in "
            "Zed settings. Nothing to update."
        )

    # Resolve which provider to update
    if not provider_id:
        if len(oai_compat) == 1:
            provider_id = next(iter(oai_compat))
        else:
            ctx.print_err(
                dedent(f"""\
                    Multiple openai_compatible providers found: {list(oai_compat.keys())}
                    Please pass [bold]--provider-id[/] to choose one.
                """)
            )
            ctx.rich_exit("")

    if provider_id not in oai_compat:
        ctx.rich_exit(
            f"Provider [bold]{provider_id}[/] not found in Zed settings. "
            f"Available providers: {list(oai_compat.keys())}"
        )

    existing_entries: list[dict] = oai_compat[provider_id].get("available_models", [])
    existing_by_name: dict[str, dict] = {e["name"]: e for e in existing_entries}

    active_set = set(active_models)
    existing_set = set(existing_by_name.keys())

    removed = existing_set - active_set
    added = active_set - existing_set

    # Build the updated list in the order returned by the proxy
    updated_entries = [
        _build_zed_model_entry(mid, existing_by_name.get(mid)) for mid in active_models
    ]

    # Locate the span of the existing available_models array in the raw text
    # and splice in the new serialization, preserving everything else verbatim.
    try:
        arr_start, arr_end = _locate_available_models_span(raw_text, provider_id)
    except KeyError as exc:
        ctx.rich_exit(f"[red]Error:[/] {exc}")

    indent_col = _detect_indent(raw_text, arr_start)
    new_array = _render_zed_models_array(updated_entries, indent_col)
    new_text = raw_text[:arr_start] + new_array + raw_text[arr_end:]

    # Back up the original file before overwriting
    backup_path = _backup(cfg_path)
    ctx.print_err(f"[dim]Backup written to {backup_path}[/]")

    cfg_path.write_text(new_text, encoding="utf-8")

    # Report changes
    if removed:
        ctx.print_err(f"[yellow]Removed {len(removed)} model(s):[/] {sorted(removed)}")
    if added:
        ctx.print_err(f"[green]Added {len(added)} model(s):[/] {sorted(added)}")
    if not removed and not added:
        ctx.print_err("[dim]Models already up to date.[/]")
    else:
        ctx.print_err(
            f"[bold green]✓[/] Updated [bold]{provider_id}[/] in {cfg_path} "
            f"({len(updated_entries)} active model(s))"
        )
