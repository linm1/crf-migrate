# Clean Sweep Loading Animation Design

## Summary

This spec defines the approved loading animation direction for the existing CRF-Migrate icon in `assets/icon.png`.

The chosen concept is **Clean Sweep**: a diagonal light band passes across the icon with moderate brightness on a short loop. The animation should feel precise, brisk, and product-grade rather than flashy or decorative.

## Goal

Create a branded loading animation that:

- uses the existing CRF-Migrate icon as the visual anchor
- communicates forward progress clearly at small and medium sizes
- works for splash/loading states, inline async waits, and action feedback
- feels distinctive to the product without becoming visually noisy

## Non-Goals

- redesigning the icon itself
- introducing multiple unrelated loader styles
- adding complex particle effects, rotation, or bounce motion
- building a decorative animation that draws more attention than the loading state

## Visual Direction

The animation keeps the icon stationary on a dark neutral backdrop while a bright diagonal highlight sweeps across it.

Key characteristics:

- the light band follows the same diagonal language as the icon
- the highlight is bright but narrow, with a soft falloff at both ends
- the motion reads as a single clean pass, not as a repeated shimmer stack
- a small warm spark accent may appear near the leading edge if it helps readability, but it is optional

## Motion Behavior

- Loop duration: `1.1s` to `1.3s`
- Motion style: linear-to-soft-eased sweep that starts cleanly, crosses the icon, and resets without a visible hitch
- Direction: diagonal sweep matching the icon’s slant
- Intensity: medium contrast, between the previously explored ambient and energetic variants
- Rhythm: fast enough to suggest active work, restrained enough to tolerate repeated viewing

## Component Behavior

The implementation should expose one canonical loading treatment built around the existing icon.

Expected behavior:

- default presentation uses the full icon with the sweep effect layered above it
- the loader must remain legible at compact sizes
- the effect should degrade gracefully if motion is reduced or disabled
- the visual should be reusable anywhere the product needs a branded loading affordance

## Accessibility

- honor reduced-motion preferences with either a static icon or a much softer, slower highlight
- preserve icon visibility throughout the full animation cycle
- avoid rapid flashing, harsh contrast jumps, or effects that could feel strobing

## Error Handling

If the animated treatment cannot load or render correctly, the fallback is the static CRF-Migrate icon with no sweep effect. The product should never block on the animation itself.

## Testing and Verification

Verification should confirm:

- the sweep reads clearly over the icon at intended sizes
- the loop does not visibly jump at reset
- reduced-motion behavior is respected
- the animation remains visually coherent on the intended background(s)
- the implementation matches the approved design panel in `ui/crf_migrat_ui.pen`

## Implementation Notes

- use `assets/icon.png` as the source artwork
- match the approved `Clean Sweep` direction documented in `ui/crf_migrat_ui.pen`
- prefer a simple implementation that is easy to reuse and tune
- keep timing, angle, and brightness adjustable through a small set of parameters if that improves maintainability
