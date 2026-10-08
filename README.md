# ComfyUI Generation History

`Generation History` is a ComfyUI image pass-through node that keeps every
queued workflow execution as one comparison row. A normal 20-image batch and
20 sequential one-image invocations both produce one run with 20 non-wrapping
thumbnails; the next Queue Prompt adds a new row below it.

It is designed for quick visual comparisons while changing checkpoints,
diffusion models, LoRAs, prompts, seeds or sampler settings without leaving the
workflow canvas.

```text
Model B comparison · SAVED · Seed: 123 · Model: model-b.safetensors
[ image ][ image ][ image ][ image ][ image ][ image ]

Model A comparison · Seed: 123 · Model: model-a.safetensors
[ image ][ image ][ image ][ image ][ image ][ image ]
```

## Features

- One Queue Prompt = one run, across normal batches and sequential/list calls.
- Oldest run first, vertical history scroll, independent horizontal row scroll.
- Full-resolution lightbox with close, `Esc`, previous and next controls.
- Automatic best-effort seed, UNet/checkpoint and LoRA metadata detection.
- Responsive rows, headers and thumbnails that follow live node resizing.
- Optional seed/model overrides and run label under the node's advanced inputs.
- Per-run delete and per-node Clear with confirmation.
- `SAVED` badge on every run stored in the ComfyUI output directory.
- No run count, image count, LRU or age-based automatic deletion.
- Optional disk persistence with atomic manifests and per-history locking.
- Unmodified `IMAGE` pass-through output.
- Separate UUID-backed history for every node instance, including duplicates.

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
- Advanced `run_label`: optional short title shown at the start of the row.

The internal history UUID is generated and saved in the workflow but hidden
from the normal node UI. When a node is duplicated, the new node receives a new
UUID instead of sharing the source node's history.

### Run grouping

Generation History groups work by ComfyUI execution identity, not by time,
seed, model, prompt text or image filename. This means:

- one Queue Prompt creates exactly one run;
- every normal batch and sequential/list invocation from that execution is
  appended to the same row in arrival order;
- two Queue Prompts with identical parameters still create two distinct runs;
- partial results remain available if a later step in the execution fails.

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
directory and the manifest exists only in the current Python process. Opening
or reloading the workflow starts a new empty non-persistent gallery, removes
the previous volatile files for that node and resets its internal identifiers.

Changing OFF → ON does not retroactively copy session rows; only later runs are
persisted. Changing ON → OFF does not delete existing output files. Rows already
visible remain visible for the current browser session. Delete and Clear remove
matching session and persistent data. Internal identifiers are not reused after
Delete or Clear within the active gallery; a newly opened non-persistent gallery
starts a fresh internal sequence.

Persistence is evaluated when each run is created; changing the checkbox does
not retroactively move earlier runs. For example, if only the third of five
runs is created with persistence enabled, only that row is stored and marked
`SAVED`. After reopening the workflow, enabling persistence loads the saved row
again. Technical run identifiers remain internal and are never displayed, so
saved and temporary sessions can coexist without confusing visible numbering.

## Data and privacy

- The node makes no external network requests and includes no telemetry.
- Generated images and manifests stay inside the configured ComfyUI `output`
  or `temp` directory.
- Images are not stored in the workflow JSON and are never included in the
  custom-node repository automatically.
- Delete removes the selected run; Clear affects only the current node's
  history UUID.

## Metadata detection

The node walks only upstream prompt links, breadth-first. It recognizes common
`seed`/`noise_seed`, `unet_name`/`ckpt_name`/`model_name`, `lora_name` and
`strength_model` fields. A UNet/diffusion loader takes precedence over a
checkpoint or generic model field; a CLIP loader is not mistaken for the main
model. Custom nodes with different field names remain usable; their unavailable
values display as `Unknown`. Overrides take precedence over automatic values.

## Manual acceptance checks

1. Queue batches of 10, 10 and 20 images in three Queue Prompts. Confirm three
   rows oldest-first, with 20 thumbnails on one horizontal strip.
2. Queue 30 runs. Confirm vertical scrolling and that no row disappears.
3. Open a thumbnail. Confirm full-resolution display, `Esc`, `×`, backdrop
   close and arrow navigation.
4. Delete a middle row. Confirm only that row and its files disappear.
5. Clear the node. Confirm other Generation History nodes are unchanged.
6. With persistence ON, restart ComfyUI and reload the workflow. Confirm the
   runs return oldest-first and numbering continues.
7. Queue an unchanged workflow twice. Confirm two new rows are created.
8. Duplicate the node. Confirm each node receives only its own later runs.
9. Use a sequential/list workflow with six one-image invocations. Confirm one
   run with six images in arrival order.
10. Use six sequential invocations with a batch of four images each. Confirm
    one run with 24 images in arrival order.
11. In a workflow with separate UNET and CLIP loaders, confirm that the main
    diffusion model is shown instead of the text encoder.
12. Resize the node between 500, 1000 and 1400 px. Confirm the gallery, header
    and the same thumbnail sizing on rows containing 1, 2 or 6 images.

## Development checks

Run from the repository root:

```bash
python -m unittest discover -s tests -v
node --check web/js/generation_history.js
python -m compileall -q .
git diff --check
```

The storage tests cover normal batches, sequential/list aggregation,
concurrent appends, persistence across storage reloads, manifest migration,
Delete, Clear and independent node histories.

## Implementation notes

The implementation uses supported ComfyUI custom-node APIs: `INPUT_TYPES`,
hidden `PROMPT`/`EXTRA_PNGINFO`/`UNIQUE_ID`, `WEB_DIRECTORY`,
`app.registerExtension`, `addDOMWidget`, `PromptServer` events, custom routes
and the current execution context's `prompt_id`. The V1 schema remains
supported by current ComfyUI and provides broad installation compatibility.

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
