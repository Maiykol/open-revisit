# open-revisit

From nominal revisit to useful observation. Open-source analytics for satellite
image availability.

This repository is being built milestone by milestone from
[`docs/SPEC.md`](docs/SPEC.md). M0 provides the reproducible Python project,
quality gates, command-line entry point, continuous integration, and container
scaffold.

## Development

Requires Python 3.12 and [`uv`](https://docs.astral.sh/uv/).

```console
uv sync
uv run open-revisit --version
make check
```
