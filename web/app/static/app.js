/**
 * RT Dashboard — minimal JS helpers.
 *
 * Plotly charts returned by HTMX partials include inline <script> tags
 * that call Plotly.newPlot(). HTMX processes inline scripts in swapped
 * content by default, so no special init logic is needed here.
 *
 * This file exists as a hook for future enhancements.
 */

// Resize Plotly charts after HTMX swaps content into the DOM
document.body.addEventListener("htmx:afterSwap", function (event) {
    var chart = event.detail.target.querySelector(".js-plotly-plot");
    if (chart) {
        Plotly.Plots.resize(chart);
    }
});
