# Background music

Put a **royalty-free** audio file here and point `BGM_PATH` in `.env` at it,
e.g. `BGM_PATH=assets\bgm\my_track.mp3`.

`placeholder_bed.mp3` is a generated low ambient drone so the BGM pipeline works
out of the box. **Replace it** — it is not meant to be shipped on real videos.

### Free, license-clear sources
- YouTube Studio → Audio Library (filter: "No attribution required")
- Pixabay Music — https://pixabay.com/music/
- Uppbeat — https://uppbeat.io/ (free tier, credit required)
- Kevin MacLeod / incompetech — https://incompetech.com/ (CC-BY, credit in description)

### Tuning (in `.env`)
| key | effect |
|---|---|
| `BGM_VOLUME_DB` | how far under the voice the bed sits. `-26` subtle, `-18` prominent |
| `BGM_DUCK` | `true` = bed automatically dips while the narrator speaks |
| `BGM_FADE_SECONDS` | fade-in / fade-out length |

Anything ffmpeg can read works (mp3, m4a, wav, ogg). A track shorter than the
video is looped automatically.
