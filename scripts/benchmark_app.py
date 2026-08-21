"""Measure cold and warm full-data Streamlit renders reproducibly."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter


def main() -> None:
    started = perf_counter()
    from streamlit.testing.v1 import AppTest

    app_path = Path("app/streamlit_app.py")
    app = AppTest.from_file(app_path).run(timeout=30)
    cold_seconds = perf_counter() - started
    if app.exception:
        raise RuntimeError(f"cold render failed: {app.exception}")

    started = perf_counter()
    app.run(timeout=30)
    warm_seconds = perf_counter() - started
    if app.exception:
        raise RuntimeError(f"warm render failed: {app.exception}")

    print(f"cold_full_render_seconds={cold_seconds:.6f}")
    print(f"warm_full_render_seconds={warm_seconds:.6f}")

    started = perf_counter()
    app.toggle(key="sensitivity_enabled").set_value(True).run(timeout=60)
    sensitivity_seconds = perf_counter() - started
    if app.exception:
        raise RuntimeError(f"sensitivity render failed: {app.exception}")
    print(f"sensitivity_full_render_seconds={sensitivity_seconds:.6f}")


if __name__ == "__main__":
    main()
