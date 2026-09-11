# Scoring quality: v2

This revision improves recognition of memorable structures rather than increasing the catalog size. Scores remain subjective preferences with explicit property evidence.

## What changed

- Completed the two-/three-run constructor to match the scorer’s rule.
- Recognized ascending/descending sequences inside repeated motifs and mirrors.
- Recognized stepping digit-runs and staircase run lengths, such as `111222333` and `122333`.
- Raised whole-sequence points from 45 to 60 and counting-block points from 35 to 45.
- Kept the strongest-rule-per-family rule; related aliases cannot multiply points.
- Broke score ties by fewer digit-runs, fewer distinct digits, then lexical order. Leading zeros receive no blanket penalty.

## Before and after on the retained catalog

| Label | Previous score | v2 score | Previous rank in length | v2 rank in length |
| --- | ---: | ---: | ---: | ---: |
| `111222333` | 26 | 56 | Not retained | 401 |
| `123123` | 51 | 81 | 570 | 6 |
| `123321` | 41 | 71 | 1730 | 43 |
| `1234321` | 35 | 65 | 5567 | 12 |
| `123454321` | 35 | 65 | Not retained | 60 |
| `12341234` | 45 | 75 | 6127 | 12 |
| `123456789` | 45 | 60 | 2310 | 72 |
| `123124125` | 35 | 45 | Not retained | 3719 |
| `122333` | 6 | 66 | Not retained | 79 |
| `112223333` | 26 | 66 | Not retained | 30 |
| `271828182` | 55 | 55 | 327 | 519 |
| `100000` | 59 | 59 | 129 | 174 |
| `121212` | 71 | 71 | 28 | 68 |

The numerical examples are regression cases. Their intended ordering is a design choice, not a market valuation. Constants and ordinary repeats remain scored consistently; they can move down when stronger new candidates enter the collection.

## Coverage and performance

- Retained 40000 names across four lengths, with 10,000 in each.
- Considered 479,668 raw emissions; candidate selection took 5.86 seconds on this machine.
- Every stored score equals the sum of its awarded property points.
- All three prior Namecheap observations survived the rebuild. No new registrar query was required.

## Limits

The curated pool is not exhaustive. The weights are deliberately inspectable and should continue to be calibrated against recognizable examples. This revision does not add cultural “lucky” preferences, personalized weights, or a resale predictor.
