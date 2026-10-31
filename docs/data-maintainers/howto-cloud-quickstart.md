# How to run the cloud quickstart

Build the default area on the shared build VM and open the result as a
public developer preview.

This uses the shared VM, which costs money. To run on a VM you administer
instead, follow [Your own VM](quickstart-cloud.md#your-own-vm-profiles).

## Before you start

- [Team access](team-access.md): the team Google Group and a signed-in
  `gcloud`.
- A Python 3 interpreter and a clone of this repository.
- The code you want built is pushed to `CLOUD_SHARED_BUILD["ref"]` (`main`)
  in [config.py](../../config.py). The VM builds that branch, not your local
  checkout.
- The preview-hosting administrator has confirmed that
  [preview hosting](team-access.md#preview-hosting-administration) is set up.
- `SOLAR_CLOUD_PROFILE` is unset.

## Steps

1. Check access at no cost:

   ```sh
   bash quickstart.sh --mode cloud --cloud preflight
   ```

2. Start the build:

   ```sh
   bash quickstart.sh --mode cloud
   ```

3. Read the printed cost estimate and its assumptions. The estimate is not a
   spending cap, and actual charges can be higher. Type `yes` to approve it.
   Any other answer stops the run before the VM starts.

4. Answer the paid-referee question. Enter declines it. The VM estimate does
   not include the referee's API cost. See
   [demolition screening](dataset-operations.md#demolition-screening).

5. Keep the terminal connected until the preview is published. Progress is
   logged as `[QS-01]` to `[QS-06]` in
   `data/cloud_quickstart_runs/<run-id>.log`.

6. Open the printed preview URL:

   ```text
   https://storage.googleapis.com/rewiring-solar-previews/devel/<github-alias>/preview.html
   ```

7. If the output starts with `WARNING:`, read the `GATE WARNING` list. It
   shows the buildings that changed most compared with the live site.

Previews are public and are deleted after 14 days. Production data is not
changed.

If a step fails, see [Troubleshooting](troubleshooting.md).

## Next steps

- [Build another region](howto-build-a-region.md) on the shared VM.
- [Build several regions](howto-scale.md) in one VM run.
- Read the cost, gate and preview details in the
  [cloud quickstart reference](quickstart-cloud.md).
- Look up run options in the [pipeline run options](reference-pipeline.md)
  reference.
