# Generation History

Generation History is an image pass-through node that collects every image
produced by one queued ComfyUI execution into a single comparison row. It works
with normal image batches and sequential/list executions.

## Inputs

- **images**: Image batch to display and pass through unchanged.
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

Use the delete button on a run to remove that run, or **Clear** to remove all
history belonging to this node. The gallery uses temporary session storage and
starts empty when the workflow is opened or reloaded.
