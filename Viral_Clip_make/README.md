# Viral Clip Make

A free, local, graphical tool that turns a long video into viral **9:16 Shorts / TikToks / Reels** —
the same idea as OpenShorts, but with **no API keys, no subscriptions, no watermarks and no cloud**.

Paste a YouTube link (or pick a local video) → click one button → get finished vertical clips with
subject tracking, word-highlight captions, a hook overlay and normalized audio, saved to a folder
on your PC.

```
link / file  →  local Whisper transcript  →  local viral-moment scoring  →  face/motion tracking
                                                                              ↓
                                            Ready clips\<date>\  MP4 + JPG + TXT + JSON
```

---

## 100% free — what that means

| Thing | This tool |
|---|---|
| Price | Free forever (MIT-style, all components open source) |
| API keys | **None required** |
| Cloud / GPU | **None required** — runs on CPU, auto-uses your NVIDIA GPU when available |
| Watermark / limits | None |
| "AI" moment detection | Local scoring engine (speech energy, keywords, pacing, hooks, silence) |
| Speech-to-text | faster-whisper, running locally |
| Face tracking | YuNet ONNX (tiny 230 KB free model) + motion fallback |
| Rendering | FFmpeg (free, open source) |

The only network access is:
1. `yt-dlp` downloading the video you asked for (cached on disk, so the same link is never
   downloaded twice).
2. A **one-time** download of the Whisper speech model (~150 MB for `base`, ~75 MB for `tiny`)
   and the 230 KB face model, both cached afterwards.

Downloads are faster now: **Balanced** uses a single-file 720p format by default (no separate audio
download or merge), **Fast** uses 480p, and **Best** uses 1080p. The progress bar and log show live
percentage, speed and time left while downloading.

---

## 1. Run it right now (source mode)

Python 3.12, FFmpeg and all Python packages are already installed on this machine.

**Double-click:** `run.bat`  (or `Start Viral Clip Make.bat`)

That opens the graphical window.

---

## 2. Build the .exe

**Double-click:** `build_exe.bat`

Close any open Viral Clip Make window first — Windows locks the old `.exe` files and the build
would otherwise keep running the previous version. The build script moves the old folder aside
automatically and reports a real error if anything fails.

It will:
- install PyInstaller (free),
- copy `ffmpeg.exe` / `ffprobe.exe` into the bundle,
- build the app.

When it finishes, double-click:

```
dist\ViralClipMake\ViralClipMake.exe
```

That's a real Windows graphical `.exe` — no Python needed on the machine that runs it.
You can copy the whole `dist\ViralClipMake` folder to another Windows PC and it works there.

> Tip: keep the first launch online so the Whisper model can download once. After that it is cached
> in `%APPDATA%\ViralClipMake\models` and works offline.

---

## 3. Use the app

1. **Video source** — paste a YouTube/online link or press **Browse file**.
2. **Settings** (defaults are fine):
   - **Clips per video** — how many shorts to make (1–15).
   - **Min / Max seconds** — clip length window (e.g. 12–45 s).
   - **Speech model** — `tiny` (fastest) → `base` (recommended) → `small` / `medium` (more accurate, slower).
     Press the **?** button in the app for a full plain-English explanation of each model.
   - **Language** — leave blank for auto-detect, or type a two-letter code such as `en`, `hi`, `es`.
   - **Video framing** — **AI picks best (recommended)** is the default: the tool measures the subject
     in every moment and chooses either a subject-following crop or a whole-frame look, with the
     subject kept slightly above the captions. If you configured an AI provider it makes the choice;
     otherwise a free local heuristic does. You can still force **Fit whole frame (blurred / black
     bars)** or **Follow subject (crops sides)** yourself.
   - **Output folder** — where finished clips are saved.
   - Toggles for face tracking (needed for AI/auto framing), captions, hook overlay and
     audio normalization and **Facecam bubble**.
    - **Facecam bubble** (off by default) - for videos with a presenter webcam in a corner of
      the original frame (the popular gaming-shorts look). The tiny free face detector spots a
      small face that stays in one corner, then puts that person in a circular inset in the top
      corner while the real subject/gameplay is cropped and centred underneath.
      **Facecam corner** can be **Auto (detect)**, **Top left** or **Top right**. If no corner webcam
      is found, the clip renders normally. The hook text moves below the circle so it stays readable.
3. Press **MAKE VIRAL CLIPS**. Watch the progress bar and activity log.
4. When it finishes, press **Open output folder** and upload the clips you like.

Processing a 10-minute video on a typical CPU takes roughly 10–20 minutes. The first run also
downloads the speech model once.

### The "AI & Output" tab

- **Optional AI / API** — by far the default is **Local (free - no key)**. If you want smarter
  moment picking and AI-written titles/hooks, choose a provider:
  - **Anthropic / Claude API** — get a key at <https://console.anthropic.com/> (it starts with
    `sk-ant-`). Choose **Anthropic / Claude API**, paste the key, and leave **Claude API URL** at
    `https://api.anthropic.com/v1`. The default model is `claude-sonnet-5-5`; if that model is not
    available on your account, the app tries a few other Claude models automatically and remembers
    the one that works. Pasting an `sk-ant-…` key switches to Claude for you.
  - **Gemini API** — get a free key at <https://aistudio.google.com/apikey>. The default model is
    `gemini-3.5-flash-lite`: measured on this machine it answers the moment-picking prompt in about
    **3 seconds**, while `gemini-3.8-flash` takes around **100 seconds** and can return an empty
    answer, which makes a run look frozen. Keep the lite model for quick feedback. Just pasting a
    Google key (`AIza…` or `AQ.…`) automatically switches the provider to Gemini. If a model is
    retired, overloaded or returns an empty body, the app automatically tries the next fastest
    available model and remembers the one that worked. Requests have a 30-second timeout, and if
    every model fails the app continues with the free local picker instead of hanging.
  - **OpenAI-compatible** — works with local **Ollama** / **LM Studio** (`http://localhost:11434/v1`,
    model e.g. `llama3.1`), **OpenRouter** (`https://openrouter.ai/api/v1`), OpenAI, or any server
    that speaks the `/chat/completions` API.
  - Keys are stored only in `%APPDATA%\ViralClipMake\settings.json` on this PC. If the API fails or
    the key is empty, the app **automatically falls back to the free local moment picker**.
- **Check connection** — the green button in the AI card performs a real, lightweight live request to
  the configured provider and shows a colored status dot: **green = connected** (with response time
  in ms), **red = failed** (with the actual error, e.g. an invalid key or unreachable server). In
  **Local** mode it confirms the free engine is ready with no API needed. Changing any key, URL or
  model resets the dot to "Not checked" so you never trust a stale result. The check runs in the
  background, so the window never freezes.
- **Hardware (GPU / CPU)** — choose **Auto (recommended)**, **CPU only**, or **GPU (NVIDIA)** for
  speech-to-text, and separately choose **Auto / CPU only / GPU only** for video encoding. The app
  detects NVIDIA CUDA and FFmpeg hardware encoders (NVENC / QuickSync / AMF) at startup and shows
  what it found. GPU acceleration can make transcription and rendering several times faster. If a
  GPU is missing or fails, the app automatically falls back to the free CPU path — no setup needed.
- **YouTube Download** — only needed if YouTube says *"Sign in to confirm you're not a bot"*.
  **Auto** (the default) tries an anonymous download first, then reads cookies from Firefox, Chrome
  and Edge until one works — Firefox is the most reliable on Windows. Pick a specific browser if you
  know where you are signed in to YouTube, or point **cookies.txt** at a Netscape-format cookie file.
  Cookies are read locally and never uploaded. **Player client** is an advanced escape hatch if a
  YouTube client change breaks downloads. The **Clean temp files now** button below deletes any
  partial downloads, half-finished clips and dead caches on demand. The same cleanup runs
  automatically the moment a download or render fails, and again every time the app starts — so an
  interrupted run never leaves gigabytes of `.part` / `.temp.mp4` fragments behind. Finished clips
  and healthy caches are always kept.
- **Save settings** — the purple **Save settings** button at the bottom of this tab writes all of
  these choices (API key/provider/model, hardware, framing, aspect ratio and speed mode) to disk
  immediately, ready to be used during every future creation. You'll see a green "✓ Saved"
  confirmation. Settings are also saved automatically whenever you press **MAKE VIRAL CLIPS**.
- **Output size** — choose `9:16` (Shorts/TikTok/Reels), `1:1`, `4:5`, `3:4`, `16:9`, or a **Custom**
  width × height. Captions, cropping and thumbnails all adapt to the chosen size.
- **Speed / Quality** — pick a mode:
  - **Fast (turbo)** — 480p source, tiny speech model, faster encode. Best for quick previews.
  - **Balanced** — recommended; 720p source, whole-frame vertical framing and clean audio.
  - **Best quality** — 1080p source, more accurate transcription and a cleaner final picture, slower render.
  The mode sets sensible defaults; you can still fine-tune the speech model and checkboxes in the
  **Make Clips** tab afterwards.

The window uses a modern rounded dark UI (CustomTkinter) with hover effects, animated fade-in,
segmented controls and toggles.

---

## 4. What you get

For every clip, in `Output folder\<date>\`:

| File | Contents |
|---|---|
| `.mp4` | Finished vertical clip (e.g. 1080×1920), H.264 + AAC, captions burned in. By default the whole source frame is visible with a blurred background |
| `.jpg` | Thumbnail |
| `.txt` | Title, viral score, hook, source timestamps, description + hashtags |
| `.json` | Full scorecard and render details |

---

## 5. How the "viral" scoring works (locally, free)

Since there is no paid AI to call, the moment picker scores candidate windows using measurable
signals from the video itself:

- **Speech density** — words per second (fast talking keeps attention).
- **Audio energy** — louder/excited sections score higher.
- **Emotion & power words** — e.g. *secret, insane, never, why, finally, truth*.
- **Hook strength** — does the opening contain a question, number, exclamation or power word?
- **Clean edges** — moments that start/end on silence or sentence boundaries.
- **Punctuation** — complete sentences beat rambling.
- **Silence penalty** — dead air lowers the score.

Overlapping candidates are removed (non-maximum suppression) so you get distinct moments, sorted
and capped at your chosen clip count.

### What makes a moment a clip (quality gate)

A high score alone is not enough. Every pick - local or AI - must have a **premise** (a person,
object or number a stranger can name before the exciting beat) and a **payoff that is seen or
heard in the last 40 %** of the window. The opening must show the moment or a reacting face, the
clip must stand alone, and two clips may not make the same point. Spoiler recaps / leak explainers
("GTA 6 has 12 endings", "the final mission", chapter-by-chapter retellings, director interviews)
are rejected outright: those Shorts get no watch time and get taken down. If nothing qualifies,
the app stops with an explanation instead of rendering ten weak clips.

Clip length is capped at **22-35 seconds** even if the UI asks for more: a single beat that finishes
beats a long setup. The AI's own 0-100 number is no longer trusted on its own - it is blended with
the measured signals using `moments.ai_weight` (default 0.6), and its sentence ends are snapped
properly (an end snaps to a segment **end**, never the next segment's start).

`python viralchoicetest.py` replays the 22 Nov failure (ten ending-spoiler picks) and confirms
every one is now rejected.

### AI-chosen clip style (best variant)

With **Video framing → AI picks best**, every moment is measured before rendering: face coverage,
motion coverage, subject position/spread and the source vs output aspect ratio. The configured AI
provider (Gemini / OpenAI-compatible; set it in the *AI & Output* tab) then picks the best look per
clip:

- **Follow subject** — crop into the vertical canvas and pan with the face/action. The crop keeps a
  margin around the subject and places it slightly above the caption area.
- **Whole frame (blurred)** — the entire picture stays visible on a blurred vertical background.
  Best for gameplay or wide action where cutting the sides would lose context.
- **Whole frame (black bars)** — clean bars instead of a blur.

No key? The same decision is made by a free local heuristic, and any AI failure automatically falls
back to it. The chosen style, its reason and its source (`ai` / `local` / `manual`) are saved in each
clip's `.txt` and `.json`.

---

## 6. Uploading (manual by design)

Open the output folder, watch the clips, delete any you don't like, then upload to YouTube Shorts,
TikTok or Instagram Reels. Vertical 1080×1920 under 3 minutes is treated as a Short automatically.
The `.txt` file has the title, description and hashtags — copy and paste them.

---

## 7. Folder layout

```
Viral_Clip make\
├─ app.py                     entry point
├─ run.bat                    run from source
├─ Start Viral Clip Make.bat  launches the .exe if built, otherwise source
├─ build.ps1 / build_exe.bat  builds the .exe
├─ requirements.txt
├─ core\                      the processing engine
│  ├─ config.py               settings (saved to %APPDATA%\ViralClipMake\settings.json)
│  ├─ downloader.py           yt-dlp download / local file
│  ├─ audio.py                audio decode + energy curve
│  ├─ transcribe.py           local Whisper transcription
│  ├─ moments.py              local viral-moment scoring engine
│  ├─ faces.py                YuNet face + motion tracking
│  ├─ captions.py             ASS caption / hook generation
│  ├─ ffmpeg_tools.py         probe, 9:16 crop, render, thumbnails
│  └─ pipeline.py             orchestrates everything
├─ gui\main_window.py         the graphical interface
├─ bin\                       ffmpeg.exe / ffprobe.exe (created by build.ps1)
└─ dist\ViralClipMake\        built Windows app (created by build.ps1)
```

---

## 8. Troubleshooting

| Problem | Fix |
|---|---|
| "FFmpeg was not found" | `winget install Gyan.FFmpeg`, then restart the app; or place `ffmpeg.exe` in `bin\`. |
| "Sign in to confirm you're not a bot" / download fails | Leave **AI & Output → YouTube Download → Browser cookies** on **Auto**: the app retries with Firefox/Chrome/Edge cookies. If that fails, choose the browser where you are signed in to YouTube (Firefox is most reliable on Windows) or supply a **cookies.txt**. Also update yt-dlp with `python -m pip install -U yt-dlp`. |
| Age-restricted / members-only video | Use the same **YouTube Download** cookies setting above; anonymous downloads cannot access these videos. |
| First run is slow / hanging | It is downloading the Whisper model once (~150 MB). Later runs are cached. |
| Captions wrong language | Set the **Language** field, e.g. `en`. |
| Face crop is off | Turn face tracking off for a centre crop, or use footage with a clearer subject. |
| Facecam bubble did not appear | The **Facecam bubble** switch only fires when a small face stays in a top corner of the original video. Check the activity log: it says whether a webcam was found. Pick **Top left** / **Top right** explicitly if Auto picked the wrong side. Videos without a webcam are rendered normally. |
| App window shows an error | Details are saved to `%APPDATA%\ViralClipMake\crash.log`. |
| Low scores / poor picks | Feed livelier footage with clear speech; try a longer min/max window. |
| Verify the built .exe | Run `dist\ViralClipMake\ViralClipMake.exe --doctor --selftest` and read `%APPDATA%\ViralClipMake\doctor.txt` and `selftest_result.txt`. |

---

## 9. Legal note

Only clip videos you own or have permission to reuse. The tool never downloads music or adds
copyrighted content — any claims from source audio remain your responsibility when you upload.
