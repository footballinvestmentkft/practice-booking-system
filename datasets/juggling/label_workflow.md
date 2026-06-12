# Juggling Dataset — Labeling Workflow v1.0

## Tools Required (all free)

| Tool | Licence | Purpose | Install |
|---|---|---|---|
| VLC Media Player | LGPL | Frame-by-frame playback | https://www.videolan.org/ |
| LabelImg | MIT | Bounding box annotation | `pip install labelImg` |
| ffmpeg | LGPL-2.1 | Frame extraction | already in project |
| Any JSON editor | — | Fill annotation template | VSCode, vim, etc. |

---

## Phase A: Eval Annotation (20 clips, ~15–30 min per clip)

### Step 1 — Video inventory

1. Copy the video to `datasets/juggling/videos/<video_id>.mp4`
2. Compute SHA-256: `sha256sum datasets/juggling/videos/<video_id>.mp4`
3. Add an entry to `dataset_manifest.json`:
   ```json
   {
     "video_id": "jug_easy_001",
     "filename": "jug_easy_001.mp4",
     "difficulty": "easy",
     "annotation_file": "annotations/jug_easy_001.json",
     "checksum_sha256": "<output from sha256sum>",
     "video_storage": "local_only",
     "video_path_local": "datasets/juggling/videos/jug_easy_001.mp4",
     "video_available": true
   }
   ```

### Step 2 — Get video metadata

```bash
ffprobe -v quiet -print_format json -show_streams -show_format \
  datasets/juggling/videos/<video_id>.mp4 | python3 -c "
import sys, json
d = json.load(sys.stdin)
for s in d['streams']:
    if s.get('codec_type') == 'video':
        print('resolution:', s.get('width'), 'x', s.get('height'))
        print('fps:', s.get('avg_frame_rate'))
        print('duration:', d['format'].get('duration'))
"
```

Note down: `resolution`, `fps`, `duration_seconds`.

### Step 3 — Juggling count (VLC)

1. Open the video in VLC
2. Set playback speed to 0.5× (Playback → Speed → Slower)
3. Watch the full clip; tap a key (or use tally on paper) for each ball touch
4. Rewind and verify once more
5. Record `total_juggling_count` and your confidence level

**Tips:**
- A juggling touch = ball changes direction due to body contact
- A dropped ball that bounces on the ground does NOT count
- If you're unsure ±1, use `count_confidence: "medium"`
- If you're unsure ±2 or more, use `count_confidence: "low"` and add a note

### Step 4 — Fill the annotation JSON

Copy `annotation_schema_v1.json`'s example block into
`annotations/<video_id>.json` and fill in all required fields:

```bash
cp datasets/juggling/annotation_schema_v1.json /tmp/template.json
# Fill in the "examples" block manually or with your editor
```

Required fields to fill:
- `video_id`, `filename`, `source`
- `duration_seconds`, `resolution`, `fps`, `orientation`
- `difficulty`
- `total_juggling_count`, `count_confidence`
- `dominant_body_part`, `body_parts_used`
- `ball_visible_quality`, `lighting_quality`, `camera_stability`
- `multi_person_present`, `multiple_balls_present`
- `expected_validity`, `invalidity_reason` (null if valid)
- `notes` (null if nothing to note)
- `annotator`, `annotation_date`, `annotation_version: "v1.0"`
- `contact_events: null` (optional in Phase A)

### Step 5 — Second-annotator cross-check (for 5 of the 20 clips)

At least 5 clips must be independently counted by a second person:

1. Second annotator watches the clip and counts independently (does NOT see the first count)
2. Both counts are recorded: `total_juggling_count` and `second_annotator_count`
3. If `|count1 - count2| <= 2`: `inter_annotator_agreement: true`
4. If `|count1 - count2| > 2`: discuss, rewatch together, agree on final count

### Step 6 — Commit annotation file

```bash
git add datasets/juggling/annotations/<video_id>.json
git add datasets/juggling/dataset_manifest.json
git commit -m "feat(dataset): annotate <video_id> — <difficulty>, count=<N>"
```

Do NOT commit the video file.

---

## Phase B1: Training Annotation (extended — for first model)

This is done AFTER Phase A is complete and a training-readiness decision is made.

### Frame Extraction

```bash
VIDEO=datasets/juggling/videos/<video_id>.mp4
OUT_DIR=datasets/juggling/frames/<video_id>
mkdir -p $OUT_DIR

# Extract every 3rd frame (10fps from 30fps source)
ffmpeg -i $VIDEO \
  -vf "select='not(mod(n,3))'" \
  -vsync 0 \
  $OUT_DIR/%04d.jpg
```

### Bounding Box Annotation (LabelImg)

```bash
labelImg datasets/juggling/frames/<video_id>/ datasets/juggling/labels/<video_id>/
```

In LabelImg:
1. Open image directory
2. Set save format to **YOLO** (menu: View → Change Save Dir, Format → YOLO)
3. For each frame: draw a tight bounding box around the ball
4. Label: `ball` (class 0)
5. Save (W key)
6. Skip frames where the ball is not visible or fully occluded

**YOLO label format** per frame (one line per object):
```
0 <cx_norm> <cy_norm> <w_norm> <h_norm>
```
Where values are normalized to [0, 1] relative to frame width/height.

### Contact Timestamp Annotation

A lightweight CLI approach using the keyboard:

```bash
python3 scripts/annotate_contact.py \
  --video datasets/juggling/videos/<video_id>.mp4 \
  --output datasets/juggling/annotations/<video_id>.json
```

The script (to be implemented in Phase B1 prep) plays the video and records:
- `SPACE` = contact event at current timestamp
- `f` = foot, `k` = knee, `t` = thigh, `s` = shoulder, `h` = head, `c` = chest
- `l` = left side, `r` = right side, `u` = unknown

Output: adds `contact_events` array to the annotation JSON.

### Export to COCO Format

```bash
python3 scripts/export_dataset.py \
  --input datasets/juggling/labels/ \
  --manifest datasets/juggling/dataset_manifest.json \
  --output datasets/juggling/training/dataset_coco.json \
  --format coco
```

---

## Annotator Calibration (Before Starting)

Before annotating the first real clip, two annotators watch the same 3 sample clips and count independently. Calibration passes if `|count1 - count2| <= 1` on all 3 clips. If not, watch together and discuss what counts as a touch.

---

## Difficulty Classification Guide

| Criterion | Easy | Medium | Hard |
|---|---|---|---|
| Lighting | Bright, even | Variable (shadow/cloud) | Dark, backlit |
| Ball visibility | Always sharp | Occasional blur | Frequent occlusion |
| Camera | Tripod | Handheld stable | Moving/tracking |
| Player size | Large (> 50% frame height) | Medium (25–50%) | Small (< 25%) |
| Body parts | Mainly foot | Foot + knee/thigh | Shoulder/head/chest |
| Duration | 10–30s | 15–60s | 20–90s |
