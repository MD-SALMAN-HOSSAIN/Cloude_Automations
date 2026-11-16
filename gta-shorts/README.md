# GTA Shorts Automator (manual posting)

Paste a gameplay link → it downloads the video, transcribes it, finds the moments most likely to
go viral, cuts them to 9:16 with face tracking, burns in captions + a hook, scores every clip, and
saves them to the **Ready clips** folder on this PC.

**Posting is manual by design.** You open the folder, watch the clips, and upload the ones you like
to YouTube yourself. No Google Cloud project, no OAuth, no API upload quota.

Runs fully local. Python + FFmpeg + a free Gemini API key. No Docker, no WSL, no subscriptions.

```
link ──► download ──► whisper words ──► Gemini picks + writes ──► 9:16 face-tracked render
                                                                        │
                                              Ready clips\<date>\*.* ◄────┘
                                                        │
                                              you upload to YouTube
```

---

## 1. What is installed

| Component | Status |
|---|---|
| Python 3.12 | installed (user scope) |
| FFmpeg 9.0 | installed via winget |
| Python packages (yt-dlp, faster-whisper, OpenCV, Gemini SDK, APScheduler) | installed |
| Project | `E:\Cloude_Automations\gta-shorts` |
| Verified by | `shorts selftest` → renders a real 1080×1920 clip, checks captions, loudness, face tracking |

The only thing you must provide is a **free Gemini API key** (step 2). YouTube login is not needed.

---

## 2. Add your free Gemini key (2 minutes)

1. Open <https://aistudio.google.com/apikey>
2. Sign in with any Google account → **Create API key** → copy it.
3. Open `E:\Cloude_Automations\gta-shorts\.env` in Notepad and make the key line look like:

```ini
GEMINI_API_KEY=AIza...your-key...
```

No quotes, no spaces around `=`. Save the file.

Verify:

```powershell
cd "E:\Cloude_Automations\gta-shorts"
.\shorts.cmd doctor --deep
```

It should print `gemini key present` and `gemini live OK`.

The free tier is enough: picking moments from a 10-minute video is a few thousand tokens.

---

## 3. Use it: paste a link

1. Open `E:\Cloude_Automations\gta-shorts`
2. Double-click **`Add Link.cmd`**
3. Paste the YouTube link → Enter

It downloads, transcribes, asks Gemini for the best moments, renders them, and opens the
**Ready clips** folder when it finishes. Processing a 10-minute video takes roughly 10–20 minutes.

You can also drag a **local video file** onto `Add Link.cmd` (your own recording — no download).

### What you get per clip

```
Ready clips\2026-11-15\2026-11-15-00123-best-moment-ever.mp4   ← the vertical Short
Ready clips\2026-11-15\2026-11-15-00123-best-moment-ever.jpg   ← thumbnail
Ready clips\2026-11-15\2026-11-15-00123-best-moment-ever.txt   ← title, description, hashtags
Ready clips\2026-11-15\2026-11-15-00123-best-moment-ever.json  ← scores + checks + source timestamps
```

Every shortlisted moment is saved (up to 6 per video), even if it scores below 72 — the score is
printed in the `.txt` so you can pick the best ones yourself. The only clips withheld are ones
Gemini flags as unsafe (safety ≤ 2/10); those go to `data\rejected\`.

Command-line equivalent:

```powershell
.\shorts.cmd add "https://youtu.be/XXXXXXXXXXX"
.\shorts.cmd open          # open the Ready clips folder
.\shorts.cmd queue         # list saved clips + scores
.\shorts.cmd show 4        # full scorecard for clip #4
```

---

## 4. Upload to YouTube manually (2 minutes per clip)

1. Open the **Ready clips** folder (it opens automatically after each run).
2. Watch the `.mp4`. Delete anything you would not watch yourself.
3. Go to <https://studio.youtube.com> → **Create → Upload videos** → pick the `.mp4`.
4. Copy the **title** from the `.txt`, paste the **description + hashtags**.
5. Select **Not made for kids**, category **Gaming**, add the thumbnail if you want.
6. YouTube automatically treats a vertical video under 3 minutes as a **Short**. Keep `#Shorts`
   in the description.
7. To keep a schedule, use **Schedule** in Studio for 09:00 / 13:00 / 18:00 / 21:00 IST instead of
   publishing everything at once.

Tip: upload from the same Google account that owns the channel, and don't post more than 4–5
Shorts a day — quality beats volume.

---

## 5. YouTube Shorts rules this respects

| Rule | How the automation handles it |
|---|---|
| Vertical 9:16, 1080×1920 | Every render is exactly 1080×1920, 30 fps, H.264 + AAC. |
| Short must be ≤ 3 minutes | Hard cap `clip_max_seconds = 55`; 15 s minimum, 22–38 s sweet spot. |
| Original / authorised content | You supply the source. Only upload gameplay you own or have permission to reuse; credit the creator in the description. |
| No reused-content spam | Every clip is a new edit with hook, captions and reframing — not a raw re-upload. Still, don't run other people's channels through it. |
| No misleading metadata | Titles/hooks come from what is actually said/seen; the rubric rejects context-dependent or exaggerated moments. |
| Legible captions | Word-level captions, 2–3 words per card, current word highlighted, max 4.6 words/second, above the Shorts UI. |
| Audio normalised | Two-pass EBU R128 to −14 LUFS / −1.5 dBTP, the level YouTube expects. |
| Advertiser-friendly | Gemini's safety score is part of the rubric; hateful/dangerous/sexual moments are rejected. |
| No third-party music | The tool never adds music. If the source contains copyrighted music, the claim is on your upload. |
| Disclosure | The edit is AI-assisted, not synthetic media; nothing is generated that pretends to be real footage. |

### The viral checklist it enforces

Every clip is scored 0–100: **60 % Gemini rubric + 40 % measured signals**.

| Criterion | Enforced by |
|---|---|
| Hook in the first second | ≤5-word ALL-CAPS hook burned in at 0.0 s with a 2.8 s pop. Clips snap to word starts; a late first word loses points. |
| Length Shorts rewards | 15–55 s hard limit, 22–38 s sweet spot; score penalises drift. |
| Face/subject centred | YuNet ONNX face detector (auto-download, 230 KB) at 2 fps + motion fallback → smoothed path → FFmpeg follows the character. |
| Readable captions | Word timestamps, yellow highlight, safe area 430 px. |
| Loud, clean audio | −14 LUFS loudnorm + light compressor. |
| No dead air | Starts on speech, ends on the payoff, head silence costs points. |
| Loopability | Gemini scores whether the last frame flows back into the first. |
| Clickable metadata | Title ≤70 chars, 5 hashtags + `#Shorts`, description written per clip. |
| Consistency | Manual: you choose the best clips and post at peak times. |

---

## 6. Feeding it

```powershell
.\shorts.cmd add "https://youtu.be/XXXX"                  # one video (processes immediately)
.\shorts.cmd add "https://youtube.com/playlist?list=..."  # first 20 videos queued
.\shorts.cmd file "D:\Gameplay\session.mp4"               # your own recording
.\shorts.cmd add "https://youtu.be/XXXX" --no-process     # queue only
.\shorts.cmd process --all                                 # render everything queued
.\shorts.cmd process --source 3                            # retry one source
```

Age-restricted videos need cookies:

```toml
[download]
cookies_from_browser = "chrome"   # or firefox / edge
# cookies_file = "C:/path/cookies.txt"
```

Rules that keep the channel alive:

- Use **your own gameplay**, or get the creator's permission and credit them.
- YouTube's reused-content policy can demonetise channels that only re-upload other people's videos.
- Do not feed it music channels or movie clips — copyright claims will kill the channel.

---

## 7. How it works (for tuning)

| Stage | File | What happens |
|---|---|---|
| Download | `gtashorts/download.py` | yt-dlp, ≤1080p MP4, reuses existing downloads, expands playlists up to `max_playlist_items` |
| Transcribe | `gtashorts/transcribe.py` | faster-whisper `base` int8, word timestamps, VAD, cached; only the first `max_minutes` analysed |
| Pick moments | `gtashorts/analyze.py` | Gemini returns JSON: start/end, title, hook, description, 5 hashtags, 6 rubric scores |
| Face track | `gtashorts/faces.py` | YuNet ONNX at 2 fps, Haar + motion fallback → median + box smoothing → RDP simplification |
| Crop path | `gtashorts/ffmpeg.py` | piecewise-linear `if(lt(t,…))` expression so FFmpeg pans the 9:16 window |
| Captions | `gtashorts/assfile.py` | hook card (top, 2.8 s) + word-highlight caption cards (safe area) |
| Render | `gtashorts/ffmpeg.py` | trim → 30 fps → tracked crop → 1080×1920 → subtitles → H.264 CRF 20 + AAC 192k, faststart |
| Score | `gtashorts/pipeline.py` | measured signals + Gemini rubric → score; clips saved either way, unsafe withheld |
| Output | `gtashorts/pipeline.py` | moves the MP4/JPG/TXT/JSON into `Ready clips\<date>\` |

### Useful knobs in `config.toml`

```toml
[paths]
output_dir = "Ready clips" # where finished clips are saved (can be an absolute path)

[video]
target_min_seconds = 22    # ideal clip length window
target_max_seconds = 38
caption_size = 86          # bigger = more viral, watch the safe area
safe_bottom_px = 430       # keep captions above the Shorts UI

[gemini]
min_viral_score = 72       # raise to 80 for only bangers, lower to 65 for volume
candidates = 12            # moments Gemini may shortlist per video

[scoring]
ai_weight = 0.60           # trust the AI vs measured signals

[download]
max_minutes = 45           # analyse only this much of a long VOD
max_playlist_items = 20    # cap when a playlist link is queued

[youtube]
enabled = false            # keep false for manual posting
```

To save clips to another drive, set an absolute path, e.g.
`output_dir = "D:/YouTube/Ready clips"`. Restart nothing — the next run uses it.

Jump starting the folder: `shorts open`.

---

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `GEMINI_API_KEY is missing` | put the key in `.env` (no quotes), save, rerun `Add Link.cmd` |
| Gemini 429 / quota | free-tier daily limit; wait, or enable billing on the key |
| Gemini returns no moments | video may have no speech/action; try a livelier source |
| `ffmpeg not found` | `winget install Gyan.FFmpeg`, then reopen the terminal |
| No clips in Ready clips folder | check the console for `Gemini ... failed`; the model name may be retired → set `gemini.model` to `gemini-3.5-flash` |
| Gemini 404 model not found | Google retires models: pick another from <https://ai.google.dev/gemini-api/docs/models> and edit `gemini.model` |
| Clip scores look low | raise `gemini.min_viral_score`? No — the score is fixed by the video; feed livelier footage |
| Whisper is slow | `whisper.model = "tiny"` in config, or feed shorter videos |
| Face crops badly | raise `crop_padding` (e.g. 0.10); keep `min_face_coverage` at 0.35 |
| Link won't download | update yt-dlp: `python -m pip install -U yt-dlp`; age-restricted needs cookies |
| Disk filling up | `shorts cleanup` (working files) and delete old folders in `Ready clips\` |
| Need to retry failures | `shorts retry` |
| Pasted the same link twice | it is skipped automatically (no duplicates); re-cut it with `shorts process --source N --force` |
| Check what is queued | `shorts status`, `shorts queue`, `shorts logs` |

---

## 9. Commands

```
shorts doctor [--deep]          health check (ffmpeg, key, disk)
shorts add <links...>           download + cut a YouTube link now
shorts file <paths...>          cut your own local recording
shorts process [--all|--source N] [--force]  render queued sources (--force re-renders a link)
shorts open                     open the Ready clips folder
shorts queue                    list saved clips + scores
shorts show <id>                full scorecard, checks and metadata
shorts status                   queue health and recent events
shorts selftest [--full]        prove the render pipeline works
shorts logs [--lines N]         event log
shorts retry                    reset failed sources/clips
shorts cleanup                  delete old working files
```

`shorts` is `shorts.cmd` in the project folder. From another directory:

```powershell
powershell -Command "& 'E:\Cloude_Automations\gta-shorts\shorts.cmd' status"
```

Optional / legacy (off unless `youtube.enabled = true`):

```
shorts post                     upload the best clip through the API
shorts auth youtube             OAuth login for automatic posting
shorts daemon                   4×/day auto-poster
shorts startup install|remove   auto-start the daemon at sign-in
```

---

## 10. Files

```
gta-shorts\
├─ Add Link.cmd            ← double-click this, paste a link
├─ shorts.cmd              ← the `shorts` command
├─ setup.ps1               ← installs everything from scratch
├─ config.toml             ← all tuning knobs
├─ .env                    ← your Gemini key (never share)
├─ gtashorts\              ← the automation
├─ Ready clips\<date>\     ← finished MP4 + JPG + TXT + JSON
└─ data\
   ├─ shorts.db            ← sources, clips, scores, events
   ├─ downloads\           ← source videos (delete these to save space)
   ├─ cache\               ← transcripts + face tracks
   ├─ work\                ← temporary render folders
   ├─ rejected\            ← clips withheld for safety (rare)
   └─ models\              ← whisper weights + YuNet face detector
```

Every clip keeps a `.txt` and `.json`, so the same edit can be cross-posted to TikTok and
Instagram Reels without re-rendering.
