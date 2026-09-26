# Solar pipeline

Run the pipeline for the configured default region, or name another region
listed in `config.REGIONS`:

```sh
bash quickstart.sh
bash quickstart.sh town_gorge_north
bash quickstart.sh pilot
```

Set `LINZ_API_KEY` in the environment or ignored `.env` before running. The
pipeline requires the local Python environment and map-build tools; see
[Local setup](data-maintainers/local-setup.md). It uses `config.PIPELINE_REGION`
when no region argument is supplied. To add or change regions, edit
`config.REGIONS` and its matching source coverage in `config.SURVEYS`.

The run prints the report and preview locations. Start with `report.md`; use
[Troubleshooting](data-maintainers/troubleshooting.md) for failures and the
[pipeline reference](data-maintainers/pipeline-reference.md) for run details.
