import holoviews as hv
import holoviews.operation.datashader as hd
import numpy as np
import pandas as pd
import panel as pn
from bokeh.models import HoverTool

from .. import config
from ..plot_helpers import customise_ticks
from ..plot_helpers import filter_points
from ..plot_helpers import hover_points
from ..plot_helpers import make_hist_on_axis
from ..plot_helpers import selected_hist


def make_muts_panel(log_y, tsm):
    plot_width = 1000
    y_dim = "time"
    if log_y:
        tsm.mutations_df["log_time"] = np.log10(1 + tsm.mutations_df["time"])
        y_dim = "log_time"

    points = tsm.mutations_df.hvplot.scatter(
        x="position",
        y=y_dim,
        hover_cols=["id", "num_parents", "num_descendants", "num_inheritors"],
    )
    points.opts(
        width=plot_width,
        height=config.PLOT_HEIGHT,
    )

    range_stream = hv.streams.RangeXY(source=points)
    streams = [range_stream]

    filtered = points.apply(filter_points, streams=streams)

    tooltips = [
        ("ID", "@id"),
        ("parents", "@num_parents"),
        ("descendants", "@num_descendants"),
        ("inheritors", "@num_inheritors"),
    ]
    hover = HoverTool(tooltips=tooltips)
    filtered.opts(
        color="num_inheritors",
        alpha="num_inheritors",
        colorbar=True,
        cmap="BuGn",
        colorbar_position="left",
        clabel="inheritors",
        tools=[hover],
    )

    hover = filtered.apply(hover_points)
    shaded = hd.datashade(filtered, width=400, height=400, streams=streams)

    main = (shaded * hover).opts(
        hv.opts.Points(tools=["hover"], alpha=0.1, hover_alpha=0.2, size=10),
    )

    time_hist = hv.DynamicMap(
        make_hist_on_axis(dimension=y_dim, points=points), streams=streams
    )
    site_hist = hv.DynamicMap(
        make_hist_on_axis(dimension="position", points=points),
        streams=streams,
    )

    breakpoints = tsm.ts.breakpoints(as_array=True)
    bp_df = pd.DataFrame(
        {
            "position": breakpoints,
            "x1": breakpoints,
            "y0": tsm.mutations_df[y_dim].min(),
            "y1": tsm.mutations_df[y_dim].max(),
        }
    )
    trees_hist = hv.DynamicMap(selected_hist(bp_df), streams=streams)
    trees_hist.opts(
        width=config.PLOT_WIDTH,
        height=100,
        hooks=[customise_ticks],
        xlabel="tree density",
    )
    trees_line = hv.Segments(bp_df, ["position", "y0", "x1", "y1"])
    trees_line.opts(
        width=config.PLOT_WIDTH,
        height=100,
        yaxis=None,
        ylabel=None,
        xlabel="tree breakpoints",
        color="grey",
        line_width=0.5,
        alpha=0.5,
    )

    layout = (main << time_hist << site_hist) + trees_hist + trees_line

    if config.ANNOTATIONS_FILE is not None:
        genes_df = tsm.genes_df(config.ANNOTATIONS_FILE)
        annot_track = make_annotation_plot(tsm, genes_df)
        layout += annot_track

    return pn.Column(layout.opts(shared_axes=True).cols(1))


def make_annotation_plot(tsm, genes_df):
    min_y = tsm.mutations_df["time"].min()
    max_y = tsm.mutations_df["time"].max()
    genes_df["y0"] = min_y + 0.3 * (max_y - min_y)
    genes_df["y1"] = max_y - 0.3 * (max_y - min_y)
    genes_rects = hv.Rectangles(
        genes_df, kdims=["position", "y0", "end", "y1"], vdims=["name", "id", "strand"]
    )
    hover_tool = HoverTool(
        tooltips=[
            ("gene name", "@name"),
            ("ensembl id", "@id"),
            ("strand", "@strand"),
        ]
    )
    genes_rects.opts(
        ylabel=None,
        line_color=None,
        shared_axes=True,
        color="maroon",
        hooks=[customise_ticks],
        width=config.PLOT_WIDTH,
        height=100,
        yaxis=None,
        xlabel="genes",
        tools=[hover_tool],
    )

    genes_rects = (
        hv.HLine(min_y + (max_y - min_y) / 2).opts(color="black", line_width=0.7)
        * genes_rects
    )
    return genes_rects


def page(tsm):
    hv.extension("bokeh")
    log_y_checkbox = pn.widgets.Checkbox(name="Log y-axis", value=False)
    muts_panel = pn.bind(make_muts_panel, log_y=log_y_checkbox, tsm=tsm)
    plot_options = pn.Column(
        pn.pane.Markdown("### Plot Options"),
        log_y_checkbox,
    )
    return pn.Column(plot_options, muts_panel)
