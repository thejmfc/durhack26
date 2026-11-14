# Further indicators, ranked

The baseline uses one indicator: the shape of the skyline. This list ranks what to add next.
"Expected gain" is a judgement, not a measurement. **Status note:** the full evaluation run has
not been executed yet, so EVALUATION.md does not exist and nothing below is measured except the
80-spot quick check in the README. Rows marked "harness ready" can be measured by running
`eval/run_eval.py`. Every indicator has a concrete way to measure its benefit
with the existing harness (`eval/run_eval.py` re-scores the stored batch photos, so most
experiments need no re-rendering).

What the quick check and inspected runs suggest we need most:

1. more skyline per fix (a single narrow photo is often ambiguous or mostly dark), and
2. something that removes the heading unknown, because sliding a short skyline over 360° is
   what lets wrong places match.

That drives the ranking.

| rank | indicator | expected information gain | effort | status |
|---|---|---|---|---|
| 1 | Several photos / panorama from one spot | very high | low | built; harness ready, not yet measured |
| 2 | Sun azimuth from shadows + timestamp → heading | high | medium | matcher side built; harness ready, not yet measured; estimator not built |
| 3 | Next-best-view suggestion | high (per extra photo) | low–medium | not built |
| 4 | Shadow patterns as landmarks | high at low sun | medium–high | not built |
| 5 | Motion between photos | medium–high | medium | not built |
| 6 | Earth's position in the sky | medium (heading + coarse position check) | low–medium | not built |
| 7 | Near-field crater rims | medium, local only | high | not built |
| 8 | Stars | low | high | not built |

## 1. Several photos or a panorama from the same spot

- **Why it helps:** more azimuth coverage means fewer places share the same horizon. Two modes
  exist: independent photos (unknown relative heading; log-likelihood maps are added) and a
  stitched set with known relative yaw (one long skyline, one unknown heading).
- **Effort:** low, and already implemented because the evaluation brief required it.
- **To measure:** `eval/run_eval.py` reports 66° vs 120° vs panorama and 1-4 photos.
- **Still to do:** estimate relative yaw between overlapping hand-held photos by matching their
  skylines in the overlap, so users do not need to shoot at fixed 60° steps.

## 2. Sun azimuth from shadow direction + timestamp (pins down heading)

- **Why it helps:** the sun's direction in the Moon-fixed frame is known exactly from the
  timestamp (ephemeris, e.g. SPICE or `astropy`). If the image gives the sun's azimuth relative
  to the camera (shadow direction of rocks, the photographer's own shadow, or the bright/dark side
  of boulders), heading follows directly and the 360° slide collapses to a few degrees.
  Near the pole the sun's azimuth changes little across a 16 km site, so one ephemeris call
  covers every candidate.
- **Effort:** medium. Ephemeris lookup is easy. The image part (robustly finding shadow
  direction and turning a ground-plane direction into an azimuth on sloped ground) is the work.
- **What was implemented (the one allowed extra):** only the matcher side, a heading window in
  `score_profiles`, exposed as `heading_hint` in `/api/locate`. The shadow-direction estimator
  itself is **not** built.
- **Upper bound (not yet run):** the harness simulates the estimator as "true heading + Gaussian
  noise" with σ = 2° and σ = 10°. This would show what a working
  estimator of that quality would buy; it does not show that we can build one.
- **How to measure the real thing:** the batch metadata already stores the sun azimuth and
  elevation per photo. Build the estimator, run it on the stored photos, report its heading
  error distribution, then feed its output instead of the simulated noise.

## 3. Next-best-view suggestion

- **Why it helps:** after a first photo the likelihood map usually has a handful of rival
  places. For each candidate direction (say every 30°) we can predict what each rival would see
  (from the horizon database) and pick the direction where the predictions differ the most,
  weighted by current likelihood. That is the photo that splits the rivals best.
- **Effort:** low–medium. All ingredients exist (candidate list + horizon profiles); it is a
  small function plus a UI arrow.
- **How to measure:** in the harness each spot already has four 66° photos at random headings.
  Compare "first photo + the stored photo nearest the suggested direction" against "first photo
  + a random other photo" on within-100 m rate. A cleaner test renders the suggested view exactly.

## 4. Shadow patterns as landmarks predicted from the sun position

- **Why it helps:** at polar sun elevations most of the scene is black, which is the main reason
  the baseline cannot use many photos ("insufficient skyline" and "shadow mistaken for sky"
  in the failure classes). But the lit/unlit pattern is itself predictable from DEM + sun direction.
  Matching the *lit* skyline (the upper edge of sunlit terrain) against a prediction that
  includes shadowing would turn the largest failure class into signal.
- **Effort:** medium–high. Needs the sun direction (timestamp), a per-position prediction of
  which horizon segments are lit (ray-march toward the sun from each skyline point), and a
  database keyed by sun direction or computed on demand for the top candidates only.
- **How to measure:** re-score the stored photos with the sun direction from the metadata;
  look at the "shadow mistaken for sky" and "insufficient skyline" rows specifically.

## 5. Motion between photos

- **Why it helps:** if the user walks a known distance and direction between two photos
  (step count, rover odometry), the two likelihood maps must agree after shifting one by that
  displacement. Near-field horizon changes quickly with position, so even 50 m of motion gives
  parallax that a wrong place will not reproduce.
- **Effort:** medium. Shift-and-multiply of likelihood maps is easy; handling uncertain
  displacement (blur the shifted map) and unknown direction (needs heading) is the real work.
- **How to measure:** the harness needs a new batch mode that renders pairs of spots a known
  vector apart. Then compare the fused error against two unrelated single photos.

## 6. Earth's position in the sky

- **Why it helps:** from a given place on the Moon the Earth sits at an almost fixed
  azimuth/elevation (it only wobbles by a few degrees with libration). If Earth is in the frame
  its azimuth gives heading, and its elevation gives latitude along the Earth direction at
  roughly 30 km per degree. Across one 16 km site that is weak for position, but it is a strong
  heading fix and a sanity check on the pitch.
- **Limits:** at the south pole the Earth is within a few degrees of the horizon and often behind
  terrain; in sunlit exposures it is a small bright disc that can saturate.
- **Effort:** low–medium (bright-disc detection + ephemeris).
- **How to measure:** add an Earth disc to the three.js render at the ephemeris direction,
  count how often it is visible above the terrain, and re-run the heading-window experiment
  with the heading it provides.

## 7. Near-field crater rims

- **Why it helps:** craters tens of metres across are resolved in the 5 m DEM and are distinctive
  locally; matching their rim ellipses could give metre-level position once the rough area is known.
- **Limits:** the craters that dominate a hand-held photo are mostly smaller than the DEM can
  show (the harness adds exactly such craters, and they act as clutter, not signal). Needs
  ellipse detection under hard shadows.
- **Effort:** high. Best treated as a refinement stage after the skyline has found the area.
- **How to measure:** restrict to photos already within 500 m and report whether the error
  shrinks; needs DEM-resolved craters in view, so stratify by that.

## 8. Stars

- **Why it helps in principle:** stars give absolute orientation (heading, pitch and roll) to
  arc-seconds, and with a horizon reference, latitude/longitude.
- **Why the value is low here:** in an exposure set for sunlit ground, stars are far below the
  noise floor, so they are simply not in the photos this product is about. A dedicated long
  exposure would work but is a different product (a star tracker).
- **Effort:** high (detection + star-catalogue matching) for little gain in sunlit scenes.
- **How to measure:** not measurable with the current renders (no star field). Would need a
  radiometric sky model first.
