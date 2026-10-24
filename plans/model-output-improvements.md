# Model Output Improvements

## Highest-value improvements

1. **Replace default `hull()` base with a tighter base outline**
   - Current default base uses a hull around all color SVGs, which can fill concavities and create large unwanted solid areas.
   - Better: use union/offset/closing of the visible color mask.
   - Keep `--base-mask` as the precise override, but make the default smarter.

2. **Add chamfers/bevels to base edges**
   - Current base has hard vertical walls.
   - Add a small bottom chamfer/roundover to reduce elephant's foot and make tiles feel nicer.
   - Add a top perimeter bevel to reduce sharp edges.

3. **Add a ribbed or hollow underside mode**
   - For large posters, a 10mm solid base is heavy, slow, and warp-prone.
   - Add an option like `--base-style solid|ribbed|shell`.
   - A 2-3mm top skin with perimeter walls and internal ribs would save filament and print time.

4. **Improve plate seam geometry**
   - Visible seams may be abrupt on multi-plate assemblies.
   - Options:
     - half-lap/step seam so adjacent plates overlap slightly
     - tongue-and-groove edges
     - chamfered seam edges
     - alignment pin holes or other non-locking registration aids
   - This could make multi-plate assemblies register better and hide gaps.

5. **Filter or thicken tiny color features**
   - Very small color islands may become fragile or unprintable.
   - Add minimum island area / minimum stroke width cleanup.
   - Possibly auto-expand tiny features to nozzle-safe widths.

6. **Make layer heights print-setting aware**
   - `raised_height=2.0` is currently generic.
   - Better defaults could snap to layer height multiples:
     - base: multiple of 0.2mm
     - color relief: maybe 0.6-1.2mm unless intentionally tactile
   - This would improve slicer predictability.

7. **Add screw/keyhole presets**
   - Current keyhole dimensions are hardcoded.
   - Add presets for common screw heads:
     - drywall screw
     - pan head
     - command strip/no-keyhole
   - Also add optional wall standoffs/bumpers so the piece hangs flatter.

8. **Add visible/hidden assembly aids**
   - Underside labels are good, but add:
     - orientation arrows
     - row/column labels near edges
     - optional front-side tiny registration marks hidden in seams
     - matching edge labels for adjacent plates

9. **Improve color boundaries**
    - Add optional slight overlap/underlap between color regions and base to avoid hairline gaps.
    - Add configurable XY compensation per color layer.

## Suggested priority

Top three to tackle first:

1. Tighter base outline
2. Chamfered/ribbed base
3. Better seam geometry

These should most noticeably improve the printed object.
