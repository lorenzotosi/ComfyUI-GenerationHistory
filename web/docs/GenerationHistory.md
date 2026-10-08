# Generation History

Generation History is an image pass-through node that collects every image
produced by one queued ComfyUI execution into a single comparison row. It works
with normal image batches and sequential/list executions.

## Inputs

- **images**: Image batch to display and pass through unchanged.
- **persist_history**: Save the gallery and full-resolution PNG files below
  `output/generation_history`. When disabled, opening or reloading the workflow
  starts an empty temporary gallery. Persistence is applied per run; saved rows
  display a `SAVED` badge while technical identifiers remain hidden.
- **seed_override**: Optional seed shown instead of automatically detected seed
  metadata.
- **model_override**: Optional model name shown instead of the detected UNet or
  checkpoint name.
- **run_label**: Optional title displayed at the start of the row.

## Usage

Connect the node after an image-producing node:

```text
VAE Decode → Generation History → Save Image
```

New runs are added at the bottom. Scroll vertically between runs and
horizontally through images in a run. Every row uses the same thumbnail size.
Select a thumbnail to open the full-resolution image.
Enabling persistence loads previously saved rows immediately; it does not save
temporary rows retroactively.

Use the delete button on a run to remove that run, or **Clear** to remove all
history belonging to this node. Persistent history has no automatic size limit.
