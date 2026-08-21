"""Deterministic matplotlib renderers for the seven M4 figures."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

import geopandas as gpd
import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
from matplotlib import pyplot as plt
from matplotlib.figure import Figure

INK = "#17202a"
MUTED = "#65737e"
NOMINAL = "#5b8ff9"
EFFECTIVE = "#e45756"
ACCENT = "#2a9d8f"


def _label(aoi_id: str, names: Mapping[str, str]) -> str:
    return names.get(aoi_id, aoi_id.replace("-", " ").title())


def _save(figure: Figure, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(
        path,
        dpi=180,
        bbox_inches="tight",
        facecolor="white",
        metadata={"Software": "open-revisit"},
    )
    plt.close(figure)
    return path


def plot_revisit_dumbbell(
    summary: pd.DataFrame, names: Mapping[str, str], path: Path
) -> Path:
    """Plot nominal and effective median revisit for each AOI."""
    ordered = summary.sort_values(
        ["effective_median_gap_days", "aoi_id"], ascending=[True, True], kind="stable"
    )
    labels = [_label(str(value), names) for value in ordered["aoi_id"]]
    nominal = ordered["nominal_median_gap_days"].to_numpy(dtype=float)
    effective = ordered["effective_median_gap_days"].to_numpy(dtype=float)
    y = np.arange(len(ordered))
    figure, axis = plt.subplots(figsize=(10.5, 8.0), constrained_layout=True)
    axis.hlines(y, nominal, effective, color="#c9d2d9", linewidth=2.0)
    axis.scatter(
        nominal, y, s=42, color=NOMINAL, label="Nominal (all complete)", zorder=3
    )
    axis.scatter(
        effective, y, s=42, color=EFFECTIVE, label="Effective (usable)", zorder=3
    )
    axis.axvline(5.0, color=INK, linestyle="--", linewidth=1.2, label="5-day reference")
    axis.set(
        yticks=y, yticklabels=labels, xlabel="Median gap between observations (days)"
    )
    axis.set_title("Clouds turn nominal acquisitions into longer effective revisit")
    axis.grid(axis="x", color="#e9ecef", linewidth=0.8)
    axis.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.08),
        frameon=False,
        ncol=3,
    )
    axis.spines[["top", "right", "left"]].set_visible(False)
    return _save(figure, path)


def plot_europe_map(
    summary: pd.DataFrame,
    aois: gpd.GeoDataFrame,
    names: Mapping[str, str],
    coastline_path: Path,
    path: Path,
) -> Path:
    """Plot AOIs over a committed offline Natural Earth reference layer."""
    coastlines = gpd.read_file(coastline_path)
    points = aois.merge(
        summary[["aoi_id", "effective_median_gap_days"]],
        on="aoi_id",
        how="inner",
        validate="one_to_one",
    )
    points = points.copy()
    points.geometry = points.geometry.representative_point()
    color_max = max(10.0, float(points["effective_median_gap_days"].max()))
    figure, axis = plt.subplots(figsize=(10.5, 8.0), constrained_layout=True)
    coastlines.plot(ax=axis, color="#f4f1ea", edgecolor="#9aa6ad", linewidth=0.55)
    plotted = points.plot(
        ax=axis,
        column="effective_median_gap_days",
        cmap="viridis",
        markersize=75,
        edgecolor="white",
        linewidth=0.7,
        vmin=0.0,
        vmax=color_max,
        legend=True,
        legend_kwds={"label": "Effective median revisit (days)", "shrink": 0.72},
    )
    label_offsets = {
        "innsbruck": (4, -10),
        "munich": (-10, 8),
        "zurich": (-22, 4),
    }
    for row in points.itertuples(index=False):
        offset = label_offsets.get(str(row.aoi_id), (3, 3))
        plotted.annotate(
            _label(str(row.aoi_id), names),
            xy=(row.geometry.x, row.geometry.y),
            xytext=offset,
            textcoords="offset points",
            fontsize=6.5,
            color=INK,
        )
    axis.set(xlim=(-27, 33), ylim=(34, 72), xlabel="Longitude", ylabel="Latitude")
    axis.set_title("Effective revisit varies across the European AOIs")
    axis.set_aspect("equal", adjustable="box")
    return _save(figure, path)


def plot_monthly_heatmap(
    monthly: pd.DataFrame, names: Mapping[str, str], path: Path
) -> Path:
    """Plot month-by-AOI P(within 7 days)."""
    pivot = monthly.pivot(index="aoi_id", columns="month", values="p_within_7d")
    pivot = pivot.loc[pivot.mean(axis=1).sort_values(ascending=False).index]
    figure, axis = plt.subplots(figsize=(11.5, 8.0), constrained_layout=True)
    image = axis.imshow(
        pivot.to_numpy(dtype=float), cmap="YlGnBu", vmin=0, vmax=1, aspect="auto"
    )
    axis.set(
        xticks=np.arange(12),
        xticklabels=[
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ],
        yticks=np.arange(len(pivot)),
        yticklabels=[_label(str(value), names) for value in pivot.index],
    )
    axis.set_title("Probability of a usable observation within seven days")
    colorbar = figure.colorbar(image, ax=axis, shrink=0.86)
    colorbar.set_label("P(within 7 days)")
    return _save(figure, path)


def plot_survival_curves(
    survival: pd.DataFrame,
    selected_aois: Sequence[str],
    names: Mapping[str, str],
    path: Path,
) -> Path:
    """Plot wait-time survival curves for deterministic range selections."""
    figure, axis = plt.subplots(figsize=(10.5, 6.5), constrained_layout=True)
    colors = plt.get_cmap("viridis")(np.linspace(0.08, 0.92, len(selected_aois)))
    for color, aoi_id in zip(colors, selected_aois, strict=True):
        rows = survival.loc[survival["aoi_id"] == aoi_id].sort_values("n_days")
        axis.step(
            rows["n_days"],
            rows["p_waiting"],
            where="post",
            linewidth=2,
            color=color,
            label=_label(aoi_id, names),
        )
    axis.set(
        xlabel="Wait threshold (days)",
        ylabel="P(waiting longer)",
        ylim=(0, 1),
        xlim=(0, int(survival["n_days"].max())),
    )
    axis.set_title("Selected AOIs span the effective-revisit range")
    axis.grid(color="#e9ecef", linewidth=0.8)
    axis.legend(frameon=False, ncol=2)
    axis.spines[["top", "right"]].set_visible(False)
    return _save(figure, path)


def plot_catalog_filter(
    observations: pd.DataFrame,
    catalog_filter: pd.DataFrame,
    *,
    min_clear: float,
    path: Path,
) -> Path:
    """Plot metadata/pixel agreement and precision-recall threshold response."""
    complete = observations.loc[observations["complete"].astype(bool)].copy()
    pooled = catalog_filter.loc[catalog_filter["aoi_id"] == "ALL"].sort_values(
        "threshold"
    )
    usable = complete["usable"].astype(bool)
    figure, (scatter_axis, curve_axis) = plt.subplots(
        1, 2, figsize=(13.0, 5.4), constrained_layout=True
    )
    scatter_axis.scatter(
        complete.loc[~usable, "catalog_cloud_cover"],
        complete.loc[~usable, "clear_fraction"],
        s=9,
        alpha=0.18,
        color=EFFECTIVE,
        label="AOI unusable",
        rasterized=True,
    )
    scatter_axis.scatter(
        complete.loc[usable, "catalog_cloud_cover"],
        complete.loc[usable, "clear_fraction"],
        s=9,
        alpha=0.22,
        color=ACCENT,
        label="AOI usable",
        rasterized=True,
    )
    scatter_axis.axvline(20, color=INK, linestyle="--", linewidth=1)
    scatter_axis.axhline(min_clear, color=INK, linestyle="--", linewidth=1)
    scatter_axis.set(
        xlim=(0, 100),
        ylim=(0, 1),
        xlabel="Catalog cloud cover (%)",
        ylabel="AOI clear fraction",
        title="Scene metadata versus AOI pixels",
    )
    scatter_axis.legend(frameon=False, loc="lower left")
    curve_axis.plot(
        pooled["threshold"],
        pooled["precision"],
        marker="o",
        label="Precision",
        color=NOMINAL,
    )
    curve_axis.plot(
        pooled["threshold"],
        pooled["recall"],
        marker="o",
        label="Recall",
        color=EFFECTIVE,
    )
    curve_axis.axvline(20, color=INK, linestyle="--", linewidth=1, label="Threshold 20")
    curve_axis.set(
        xlim=(0, 100),
        ylim=(0, 1),
        xlabel="Catalog cloud-cover threshold (%)",
        ylabel="Rate",
        title="Pooled catalog-filter performance",
    )
    curve_axis.grid(color="#e9ecef", linewidth=0.8)
    curve_axis.legend(frameon=False)
    for axis in (scatter_axis, curve_axis):
        axis.spines[["top", "right"]].set_visible(False)
    return _save(figure, path)


def plot_service_level(
    service: pd.DataFrame, names: Mapping[str, str], path: Path
) -> Path:
    """Plot SLA success curves for all AOIs and their cross-AOI median."""
    figure, axis = plt.subplots(figsize=(10.5, 6.5), constrained_layout=True)
    for aoi_id in sorted(set(service["aoi_id"]) - {"MEDIAN"}):
        rows = service.loc[service["aoi_id"] == aoi_id]
        axis.plot(
            rows["window_days"],
            rows["success_rate"],
            color=MUTED,
            alpha=0.30,
            linewidth=0.8,
        )
    median = service.loc[service["aoi_id"] == "MEDIAN"]
    axis.plot(
        median["window_days"],
        median["success_rate"],
        color=EFFECTIVE,
        linewidth=3,
        label="Median across AOIs",
    )
    axis.set(
        xlim=(1, 30),
        ylim=(0, 1),
        xlabel="Required interval W (days)",
        ylabel="P(wait < W)",
    )
    axis.set_title("Service-level success rises with the allowed wait")
    axis.grid(color="#e9ecef", linewidth=0.8)
    axis.legend(frameon=False)
    axis.spines[["top", "right"]].set_visible(False)
    return _save(figure, path)


def plot_rgb_examples(
    examples: pd.DataFrame,
    chips: Mapping[str, np.ndarray],
    names: Mapping[str, str],
    path: Path,
) -> Path:
    """Plot the two selected RGB metadata/pixel disagreement chips."""
    figure, axes = plt.subplots(1, 2, figsize=(12.5, 6.0), constrained_layout=True)
    titles = {
        "catalog-clear_aoi-cloudy": "Catalog says clear; AOI is cloudy",
        "catalog-cloudy_aoi-clear": "Catalog says cloudy; AOI is clear",
    }
    for axis, row in zip(axes, examples.itertuples(index=False), strict=True):
        case = str(row.case)
        axis.imshow(chips[case])
        axis.set_title(titles[case], fontsize=12)
        axis.set_xlabel(
            f"{_label(str(row.aoi_id), names)} · catalog "
            f"{cast(float, row.catalog_cloud_cover):.1f}% · "
            f"AOI clear {cast(float, row.clear_fraction):.1%}"
        )
        axis.set_xticks([])
        axis.set_yticks([])
    figure.suptitle(
        "Scene-level metadata can disagree with the target AOI", fontsize=15
    )
    return _save(figure, path)
