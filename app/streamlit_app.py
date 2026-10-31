"""Berlin property scanner.

Run with: uv run streamlit run app/streamlit_app.py
"""

import sys
from pathlib import Path

import streamlit as st

# Community Cloud installs requirements.txt only. The library lives in src/.
_src = Path(__file__).resolve().parents[1] / "src"
if _src.is_dir() and str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

st.set_page_config(page_title="Berlin property scanner", layout="wide")

# Deck.gl measures the hover box from a zero-height strip that sits under the
# map, so the box is drawn one map-height too high and runs off the right edge.
st.html(
    """
<style>
.deck-widget-container {
  position: absolute !important;
  inset: 0 !important;
  width: 100% !important;
  height: 100% !important;
  pointer-events: none !important;
}
.deck-tooltip {
  max-width: min(400px, calc(100% - 16px)) !important;
  max-height: calc(100% - 16px) !important;
  overflow: hidden !important;
  box-sizing: border-box !important;
  white-space: pre-line !important;
  overflow-wrap: break-word !important;
}
</style>
"""
)
st.html(
    r"""
<script>
(function () {
  if (window.__immoMapTooltip) return;
  window.__immoMapTooltip = true;
  var pad = 8;
  function clamp(tip) {
    if (!tip || tip.style.display === "none") return;
    var parent = tip.offsetParent;
    if (!parent) return;
    var match = /translate\(\s*(-?[\d.]+)px,\s*(-?[\d.]+)px\s*\)/.exec(
      tip.style.transform || ""
    );
    if (!match) return;
    var rawX = parseFloat(match[1]);
    var rawY = parseFloat(match[2]);
    var maxX = Math.max(pad, parent.clientWidth - tip.offsetWidth - pad);
    var maxY = Math.max(pad, parent.clientHeight - tip.offsetHeight - pad);
    var x = Math.min(Math.max(rawX, pad), maxX);
    var y = Math.min(Math.max(rawY, pad), maxY);
    if (Math.abs(x - rawX) < 0.5 && Math.abs(y - rawY) < 0.5) return;
    tip.style.transform = "translate(" + x + "px, " + y + "px)";
  }
  new MutationObserver(function () {
    document.querySelectorAll(".deck-tooltip").forEach(clamp);
  }).observe(document.body, {
    subtree: true,
    attributes: true,
    attributeFilter: ["style"],
  });
})();
</script>
""",
    unsafe_allow_javascript=True,
)
st.html(
    r"""
<script>
(function () {
  if (window.__immoFlatClick) return;
  window.__immoFlatClick = true;
  document.addEventListener("click", function (event) {
    var node = event.target;
    if (!node || !node.closest) return;
    var mark = node.closest("[aria-label]");
    if (!mark) return;
    var label = mark.getAttribute("aria-label") || "";
    var match = label.match(/\/flat\?listing=\d+/);
    if (!match) return;
    event.preventDefault();
    event.stopPropagation();
    window.open(match[0], "_blank", "noopener");
  }, true);
})();
</script>
""",
    unsafe_allow_javascript=True,
)

st.navigation(
    [
        st.Page("pages/flats.py", title="Flats", default=True),
        st.Page("pages/flat.py", title="Flat detail"),
        st.Page("pages/gymnasiums.py", title="Gymnasiums"),
        st.Page("pages/sekundarschulen.py", title="Sekundarschulen"),
        st.Page("pages/grundschulen.py", title="Grundschulen"),
        st.Page("pages/demographics.py", title="Demographics"),
        st.Page("pages/scores.py", title="Scores"),
        st.Page("pages/price_model.py", title="Price model"),
        st.Page("pages/area_price.py", title="Area price model"),
        st.Page("pages/location_model.py", title="Location model"),
        st.Page("pages/gaps.py", title="Data gaps"),
    ]
).run()
