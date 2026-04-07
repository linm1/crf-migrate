"""Reusable animated loader component for CRF-Migrate.

Renders an SVG "Stacked Pages" animation using the app's brand identity
(parallelogram shapes in pink/magenta/orange, floating upward sequentially).
"""


def loader_html(message: str = "Processing…") -> str:
    """Return the full HTML string for the animated Stacked Pages loader.

    Args:
        message: Label shown below the animation in a muted style.

    Returns:
        HTML string suitable for passing to ``placeholder.html()``.
    """
    return f"""
<div class="crf-loader-wrap">
  <style>
    .crf-loader-wrap {{
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 32px 0 24px;
    }}
    .crf-loader-svg {{
      overflow: visible;
    }}
    @keyframes crf-pageFloat {{
      0%   {{ transform: translateY(0px);  opacity: 1.0;  }}
      30%  {{ transform: translateY(-8px); opacity: 0.75; }}
      60%  {{ transform: translateY(0px);  opacity: 1.0;  }}
      100% {{ transform: translateY(0px);  opacity: 1.0;  }}
    }}
    .crf-layer-bottom {{
      animation: crf-pageFloat 1200ms cubic-bezier(0.5, -0.1, 0.5, 1.1) infinite;
      animation-delay: 200ms;
    }}
    .crf-layer-middle {{
      animation: crf-pageFloat 1200ms cubic-bezier(0.5, -0.1, 0.5, 1.1) infinite;
      animation-delay: 100ms;
    }}
    .crf-layer-top {{
      animation: crf-pageFloat 1200ms cubic-bezier(0.5, -0.1, 0.5, 1.1) infinite;
      animation-delay: 0ms;
    }}
    .crf-loader-label {{
      margin-top: 16px;
      font-family: 'Aeonik Mono', ui-monospace, monospace;
      font-size: 12px;
      font-weight: 400;
      color: #818181;
      letter-spacing: 0.5px;
      text-transform: uppercase;
    }}
  </style>

  <!-- SVG canvas: ~90×90px, parallelograms at ~15° skew, stacked with 8px/6px offsets -->
  <svg class="crf-loader-svg" width="90" height="90" viewBox="0 0 90 90"
       xmlns="http://www.w3.org/2000/svg">

    <!-- Bottom layer: Orange #FF9800 — delay 200ms -->
    <g class="crf-layer-bottom">
      <polygon
        points="27,34 87,34 77,78 17,78"
        fill="#FF9800"
        fill-opacity="0.92"
      />
    </g>

    <!-- Middle layer: Magenta #B5135A — delay 100ms -->
    <g class="crf-layer-middle">
      <polygon
        points="21,21 81,21 71,65 11,65"
        fill="#B5135A"
        fill-opacity="0.90"
      />
    </g>

    <!-- Top layer: Pink #E91E8C — delay 0ms -->
    <g class="crf-layer-top">
      <polygon
        points="15,8 75,8 65,52 5,52"
        fill="#E91E8C"
        fill-opacity="0.95"
      />
    </g>

  </svg>

  <div class="crf-loader-label">{message}</div>
</div>
"""


def show_loader(placeholder, message: str = "Processing…") -> None:
    """Render the animated Stacked Pages loader into a Streamlit empty placeholder.

    Args:
        placeholder: A Streamlit empty placeholder (``st.empty()``).
        message: Label shown below the animation in a muted style.
    """
    placeholder.html(loader_html(message))


def clear_loader(placeholder) -> None:
    """Clear the animated loader from the given Streamlit placeholder.

    Args:
        placeholder: The same Streamlit empty placeholder passed to ``show_loader``.
    """
    placeholder.empty()
