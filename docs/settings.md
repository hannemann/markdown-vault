# Settings reference

Generated from `src/markdown_vault/core/settings.schema.json` — **do not edit by hand**; run `make docs-settings`. In `settings.yaml` these nest one branch per domain (`ask:`, `semantic:`, …); `_DEFAULT_SETTINGS` in `core/config.py` is the runtime source of truth, kept in step with the schema by `tests/test_settings_schema.py`.

## autosave

| Setting | Type | Default | Description |
| --- | --- | --- | --- |
| `autosave.interval` | integer | `30` | Seconds between automatic saves of a modified note. |

## view

| Setting | Type | Default | Description |
| --- | --- | --- | --- |
| `view.default_mode` | string | `"edit"` | Which view a note opens in: edit, render or split. Values: `edit`, `render`, `split`. |

## hide_deprecated

| Setting | Type | Default | Description |
| --- | --- | --- | --- |
| `hide_deprecated` | boolean | `false` | Hide notes marked deprecated from the vault tree AND every search surface (a visible 'N hidden' notice remains). Spans pages, so it lives at the top level. |

## editor

| Setting | Type | Default | Description |
| --- | --- | --- | --- |
| `editor.font_size` | integer | `14` | Editor font size in points. |
| `editor.tab_width` | integer | `4` | Spaces a tab occupies in the editor. |
| `editor.wrap_text` | boolean | `true` | Soft-wrap long lines in the editor. |

## preview

| Setting | Type | Default | Description |
| --- | --- | --- | --- |
| `preview.zoom` | number | `1.0` | Preview zoom factor (1.0 = 100%). |
| `preview.allow_remote_images` | boolean | `false` | Allow the preview to load images from remote URLs. Off by default, so a note cannot phone home when rendered. |

## tabs

| Setting | Type | Default | Description |
| --- | --- | --- | --- |
| `tabs.min_width` | integer | `150` | Minimum tab width in pixels before the label ellipsizes. |
| `tabs.wrap` | boolean | `false` | Wrap the tab bar onto multiple rows instead of scrolling it. |
| `tabs.switch_mode` | string | `"mru"` | Ctrl+Tab order: mru (most-recently-used switcher) or cycle (plain tab order). Values: `mru`, `cycle`. |
| `tabs.keybinding.next` | string | `"<Control>Tab"` | Accelerator that selects the next tab. |
| `tabs.keybinding.prev` | string | `"<Shift><Control>Tab"` | Accelerator that selects the previous tab. |

## log

| Setting | Type | Default | Description |
| --- | --- | --- | --- |
| `log.level` | string | `"info"` | Application log level, effective after restart. Values: `debug`, `info`, `warning`, `error`. |
| `log.third_party` | string | `"warning"` | Log level for third-party libraries. Values: `debug`, `info`, `warning`, `error`. |
| `log.glib` | string | `"critical"` | Log level for GLib/GTK messages. Values: `all`, `debug`, `info`, `message`, `warning`, `critical`, `error`. |

## webkit

| Setting | Type | Default | Description |
| --- | --- | --- | --- |
| `webkit.disable_dmabuf` | boolean | `false` | Export WEBKIT_DISABLE_DMABUF_RENDERER=1 at startup — a workaround for a black or broken preview on some GPU driver stacks. |
| `webkit.disable_compositing` | boolean | `false` | Export WEBKIT_DISABLE_COMPOSITING_MODE=1 at startup — a workaround for preview rendering glitches on some drivers. |

## wikilink

| Setting | Type | Default | Description |
| --- | --- | --- | --- |
| `wikilink.autofix_normalize` | boolean | `false` | On manual save, normalize whitespace inside [[wikilinks]]. |
| `wikilink.autofix_relink` | boolean | `false` | On manual save, redirect a broken [[wikilink]] when exactly one vault file matches its basename (moved/renamed/casing). |
| `wikilink.warn_on_save` | boolean | `false` | After a manual save, inform about [[wikilinks]] that stay unresolved. |
| `wikilink.mark_broken` | boolean | `false` | Mark broken [[wikilinks]] live in the editor (gutter warning triangle + red underline). |

## semantic

| Setting | Type | Default | Description |
| --- | --- | --- | --- |
| `semantic.enabled` | boolean | `false` | Enable semantic (vector) search and the Ask feature. Opt-in; off by default. |
| `semantic.backend` | string | `"onnx"` | Embedding backend: onnx (local, recommended), ollama (server) or openai (an OpenAI-compatible embeddings server). Values: `onnx`, `ollama`, `openai`. |
| `semantic.min_score` | number | `0.35` | Minimum similarity score for a passage to count as a semantic hit. |
| `semantic.ollama.url` | string | `"http://localhost:11434"` | Ollama server URL used for embeddings. |
| `semantic.ollama.model` | string | `"nomic-embed-text"` | Ollama embedding model name. |
| `semantic.openai.url` | string | `"http://localhost:8080"` | OpenAI-compatible embeddings server URL (POST /v1/embeddings). |
| `semantic.openai.model` | string | `""` | Embedding model name for the OpenAI-compatible server. Empty is a configured-but-unusable state the UI names — no general default exists. |
| `semantic.onnx.dir` | string | `""` | Folder holding the ONNX files (model.onnx + tokenizer.json). Empty falls back to the app data dir default. |
| `semantic.onnx.model_url` | string | `"https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2/resolve/main/onnx/model.onnx"` | Download URL for the ONNX embedding model. |
| `semantic.onnx.tokenizer_url` | string | `"https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2/resolve/main/tokenizer.json"` | Download URL for the ONNX tokenizer.json. |

## ask

| Setting | Type | Default | Description |
| --- | --- | --- | --- |
| `ask.engine` | string | `"auto"` | Answer engine: auto (the app configures backend, GPU offload and thread count), manual (honour the advanced ask.backend / ask.local settings) or off (no answers). Values: `auto`, `manual`, `off`. |
| `ask.backend` | string | `"local"` | Manual-mode chat backend: local (in-process GGUF via llama-cpp-python), ollama or openai (an OpenAI-compatible server). Only takes effect when ask.engine is manual. Values: `local`, `ollama`, `openai`. |
| `ask.reasoning` | boolean | `true` | Let a reasoning model think before answering. Accurate but slower; only sent to the backend when False, so non-reasoning models are unaffected. |
| `ask.hybrid` | boolean | `true` | Fuse a BM25 keyword ranking into the semantic retrieval, so exact tokens embeddings blur still surface. |
| `ask.top_k` | integer | `10` | How many notes are retrieved as context for an answer. Fewer is much faster on CPU. |
| `ask.num_ctx` | integer | `8192` | Context window in tokens requested from the local and Ollama backends. The OpenAI backend sizes its context server-side and ignores this. |
| `ask.max_tokens` | integer | `1024` | Hard cap on generated tokens per answer, so a repetition loop still stops. |
| `ask.system_prompt` | string | `""` | Override the built-in Ask system prompt. Empty uses the built-in default. |
| `ask.server.url` | string | `"http://localhost:11434"` | Active server URL for the current server backend (ollama or openai). |
| `ask.server.model` | string | `"llama3.2"` | Model name sent to the server backend. |
| `ask.server.model_by_endpoint` | object | `{}` | Remembered model per '<backend>\|<url>' endpoint, so switching provider restores that provider's model. Managed by the app. |
| `ask.server.url_by_backend` | object | `{}` | Remembered server URL per backend, so switching does not point the new backend at the old host. Managed by the app. |
| `ask.local.n_gpu_layers` | integer | `0` | Transformer layers to offload to the GPU for the local backend. 0 = pure CPU (safe default). |
| `ask.local.n_threads` | integer | `0` | CPU threads for the local backend. 0 = half the physical cores, resolved at runtime. |
| `ask.local.n_batch` | integer | `0` | Logical prompt batch size (max tokens per decode). 0 = llama.cpp default. |
| `ask.local.n_ubatch` | integer | `0` | Physical prompt micro-batch size — the prefill-speed lever on the GPU. 0 = llama.cpp default; keep <= n_batch. |
| `ask.local.kv_type_k` | string | `"f16"` | KV-cache precision for the K cache. Quantizing K is free. Values: `f16`, `q8_0`, `q4_0`. |
| `ask.local.kv_type_v` | string | `"f16"` | KV-cache precision for the V cache. Below f16 requires flash attention. Values: `f16`, `q8_0`, `q4_0`. |
| `ask.local.flash_attn` | boolean | `false` | Enable flash attention in the local backend. |
| `ask.local.use_mmap` | boolean | `true` | Memory-map the model file. Off loads it fully into RAM (slower first load, no page-faults during the answer). |
| `ask.gguf.filename` | string | `""` | GGUF filename inside ask.gguf.dir for the local backend (only the basename is used). Empty picks the newest model in the folder. |
| `ask.gguf.url` | string | `"https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF/resolve/main/Llama-3.2-3B-Instruct-Q4_K_M.gguf"` | Pre-fill URL for the GGUF download button. |
| `ask.gguf.dir` | string | `""` | Folder the Ask GGUF picker searches and downloads into. Empty uses the shared models dir; kept separate from the Whisper models. |

## document

| Setting | Type | Default | Description |
| --- | --- | --- | --- |
| `document.whisper_model` | string | `"base"` | Whisper model size for audio transcription on import. Bigger is more accurate, slower and a larger download. Values: `tiny`, `base`, `small`, `medium`, `large-v3`. |
| `document.import_last_dir` | string | `""` | Directory the document-import file chooser reopens in. Managed by the app. |

## debug

| Setting | Type | Default | Description |
| --- | --- | --- | --- |
| `debug.active` | boolean | `false` | Developer flag: turn on extra diagnostic behaviour. For troubleshooting only. |
| `debug.dump` | object | `{}` | Developer flags: write a debug dump per named component (file_index, backlink_index, preview_html, vault_tree, tabs, sidebar, …). An open map — any component name is valid, its value must be boolean. For troubleshooting only. |

## Keys that depend on others

A per-key schema cannot express when one setting only takes effect under another, so those couplings are listed here:

- **`ask.backend`** (depends on `ask.engine`) — Only applies when ask.engine is 'manual'. With ask.engine 'auto' (the default) the app picks the backend and ask.backend is ignored.
- **`ask.server.*`** (depends on `ask.backend`) — The server url/model are read only when ask.backend is 'ollama' or 'openai'.
- **`ask.local.*`** (depends on `ask.backend`) — The llama.cpp runtime knobs are read only when ask.backend is 'local'.
- **`ask.gguf.*`** (depends on `ask.backend`) — The local GGUF file/folder are read only when ask.backend is 'local'.
- **`ask.local.kv_type_v`** (depends on `ask.local.flash_attn`) — Quantizing the V cache below f16 requires flash_attn: true.
