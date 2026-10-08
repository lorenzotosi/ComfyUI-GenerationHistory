# ComfyUI Generation History

`Generation History` is a ComfyUI image pass-through node that keeps every
queued workflow execution as one comparison row. A normal 20-image batch and
20 sequential one-image invocations both produce one run with 20 non-wrapping
thumbnails; the next Queue Prompt adds a new row above it.

## Features

- One Queue Prompt = one run, across normal batches and sequential/list calls.
- Newest run first, vertical history scroll, independent horizontal row scroll.
- Full-resolution lightbox with close, `Esc`, previous and next controls.
- Automatic best-effort seed, UNet/checkpoint and LoRA metadata detection.
- Responsive rows, headers and thumbnails that follow live node resizing.
- Optional seed/model overrides and run label under the node's advanced inputs.
- Per-run delete and per-node Clear with confirmation.
- No run count, image count, LRU or age-based automatic deletion.
- Optional disk persistence with atomic manifests and per-history locking.
- Unmodified `IMAGE` pass-through output.
- Separate UUID-backed history for every node instance, including duplicates.

The implementation uses the currently supported ComfyUI custom-node APIs:
`INPUT_TYPES`, hidden `PROMPT`/`EXTRA_PNGINFO`/`UNIQUE_ID`, `WEB_DIRECTORY`,
`app.registerExtension`, `addDOMWidget`, `PromptServer` events, custom routes
and the current execution context's `prompt_id`. The V1 schema remains fully
supported by current ComfyUI and gives this node wider installation
compatibility than requiring the newer V3 schema.

## Installation

Clone the repository inside `custom_nodes`:

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/lorenzotosi/ComfyUI-GenerationHistory.git
```

Alternatively, download the repository ZIP and extract the whole directory to:

```text
ComfyUI/custom_nodes/ComfyUI-GenerationHistory/
```

The resulting layout is:

```text
ComfyUI-GenerationHistory/
├── .comfyignore
├── __init__.py
├── generation_history.py
├── pyproject.toml
├── storage.py
├── web/
│   ├── docs/
│   │   └── GenerationHistory.md
│   └── js/
│       └── generation_history.js
├── tests/
│   ├── test_generation_history.py
│   └── test_storage.py
└── README.md
```

No extra `requirements.txt` is needed. The implementation only uses Python,
aiohttp, NumPy and Pillow components already required by ComfyUI.

Restart ComfyUI after copying the directory.

## Compatibility

- Python 3.10 or newer.
- Current ComfyUI releases that provide the execution context API.
- Windows, macOS and Linux.

The node has no third-party dependencies beyond packages already provided by
ComfyUI.

## License

Released under the [MIT License](LICENSE). You may use, modify and redistribute
the project under its terms.

## Usage

Add **Generation History** from `image/history` and connect it like this:

```text
VAE Decode → Generation History → Save Image
```

The node is also an output node, so it executes when it has no downstream
consumer. It deliberately returns an always-changing cache fingerprint, which
means queuing the same workflow twice records two distinct runs.

Inputs:

- `images`: the `IMAGE` batch received in the current invocation. Repeated
  invocations from the same Queue Prompt are appended to the same run.
- `persist_history`: stores new runs permanently when enabled.
- Advanced `seed_override`: blank uses automatic detection.
- Advanced `model_override`: blank uses automatic detection.
- Advanced `run_label`: optional short label shown after the run number.

The internal history UUID is generated and saved in the workflow but hidden
from the normal node UI. When a node is duplicated, the new node receives a new
UUID instead of sharing the source node's history.

## Persistence

With persistence enabled, data is stored below the active ComfyUI output
directory:

```text
output/generation_history/<history-uuid>/
├── manifest.json
├── run_000001/
│   ├── image_000.png
│   └── image_001.png
└── run_000002/
    └── image_000.png
```

`manifest.json` is written through a flushed temporary file and atomically
replaced. A corrupt manifest is preserved as `manifest.corrupt-*.json`; it does
not prevent ComfyUI from starting. Run allocation, delete and clear operations
are locked per history UUID. Manifest version 2 records the execution ID, all
distinct seeds and the stable index of every image. Version 1 histories are
read and migrated without deleting their images.

With persistence disabled, full-resolution PNGs are placed in ComfyUI's temp
directory and the manifest exists only in the current Python process. A browser
reload can restore that session history, while a ComfyUI restart starts with an
empty non-persistent gallery.

Changing OFF → ON does not retroactively copy session rows; only later runs are
persisted. Changing ON → OFF does not delete existing output files. Rows already
visible remain visible for the current browser session. Delete and Clear remove
matching session and persistent data. Run numbers are not reused after Delete
or Clear.

## Metadata detection

The node walks only upstream prompt links, breadth-first. It recognizes common
`seed`/`noise_seed`, `unet_name`/`ckpt_name`/`model_name`, `lora_name` and
`strength_model` fields. A UNet/diffusion loader takes precedence over a
checkpoint or generic model field; a CLIP loader is not mistaken for the main
model. Custom nodes with different field names remain usable; their unavailable
values display as `Unknown`. Overrides take precedence over automatic values.

## Manual acceptance checks

1. Queue batches of 10, 10 and 20 images in three Queue Prompts. Confirm rows
   `#3`, `#2`, `#1`, with 20 thumbnails on one horizontal strip.
2. Queue 30 runs. Confirm vertical scrolling and that no row disappears.
3. Open a thumbnail. Confirm full-resolution display, `Esc`, `×`, backdrop
   close and arrow navigation.
4. Delete a middle row. Confirm only that row and its files disappear and the
   next run number is not reused.
5. Clear the node. Confirm other Generation History nodes are unchanged.
6. With persistence ON, restart ComfyUI and reload the workflow. Confirm the
   runs return newest-first and numbering continues.
7. Queue an unchanged workflow twice. Confirm two new rows are created.
8. Duplicate the node. Confirm each node receives only its own later runs.
9. Use a sequential/list workflow with six one-image invocations. Confirm one
   run with six images in arrival order.
10. Resize the node between 500, 1000 and 1400 px. Confirm the gallery, header
    and thumbnail sizing update immediately.

## Troubleshooting

- Restart ComfyUI after installation or Python changes.
- Hard-refresh the browser (`Ctrl+Shift+R`, or `Cmd+Shift+R` on macOS) after
  JavaScript changes.
- Check the server log for lines prefixed with `[GenerationHistory]`.
- Check the browser developer console for frontend or HTTP errors.
- Verify the ComfyUI process can write to its configured `output` and `temp`
  directories.
- Metadata shown as `Unknown` is not fatal; use the advanced overrides for
  custom sampler or loader nodes whose field names are not recognized.
