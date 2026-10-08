# Generation History

Generation History is an image pass-through node that collects every image
produced by one queued ComfyUI execution into a single comparison row. It works
with normal image batches and sequential/list executions.

## Inputs

- **images**: Image batch to display and pass through unchanged.
- **persist_history**: Save the gallery and full-resolution PNG files below
  `output/generation_history`. When disabled, history lasts only for the current
  ComfyUI server session.
- **seed_override**: Optional seed shown instead of automatically detected seed
  metadata.
- **model_override**: Optional model name shown instead of the detected UNet or
  checkpoint name.
- **run_label**: Optional label displayed beside the run number.

## Usage

Connect the node after an image-producing node:

```text
VAE Decode → Generation History → Save Image
```

The newest run appears first. Scroll vertically between runs and horizontally
through images in a run. Select a thumbnail to open the full-resolution image.

Use the delete button on a run to remove that run, or **Clear** to remove all
history belonging to this node. Persistent history has no automatic size limit.
