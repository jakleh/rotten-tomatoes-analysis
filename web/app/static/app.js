/**
 * RT Dashboard — minimal JS helpers.
 *
 * Plotly charts returned by HTMX partials include inline <script> tags
 * that call Plotly.newPlot(). HTMX processes inline scripts in swapped
 * content by default, so no special init logic is needed here.
 *
 * This file exists as a hook for future enhancements.
 */

// Re-process any Plotly charts after HTMX swaps content
document.body.addEventListener("htmx:afterSwap", function (event) {
    // Plotly inline scripts are auto-executed by HTMX.
    // If we later need to resize or reflow charts, handle it here.
});
