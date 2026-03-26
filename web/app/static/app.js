/**
 * RT Dashboard — minimal JS helpers.
 *
 * Plotly charts are rendered with autosize:false and explicit dimensions.
 * This file handles resizing on window resize and after HTMX content swaps.
 */

// Resize Plotly chart to fit container width (preserving layout height)
function resizePlotlyChart() {
    var chart = document.querySelector(".js-plotly-plot");
    if (chart && chart.parentElement) {
        Plotly.relayout(chart, {width: chart.parentElement.clientWidth - 32});
    }
}

window.addEventListener("resize", resizePlotlyChart);

document.body.addEventListener("htmx:afterSwap", function (event) {
    var chart = event.detail.target.querySelector(".js-plotly-plot");
    if (chart) {
        resizePlotlyChart();
    }
});
