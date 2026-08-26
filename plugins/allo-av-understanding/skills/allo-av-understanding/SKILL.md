---
name: allo-av-understanding
description: Use the Allo audio/video understanding HTTP API to submit media files or downloadable public video URLs, poll asynchronous jobs, retrieve ASR/OCR timeline evidence, generate summaries, translate extracted evidence, answer questions grounded in video evidence, and produce evidence-based presentation or speech distillation reports.
tools: []
version: "1.0.0"
author: lemon7Cy
---

# Allo Audio/Video Understanding

## Purpose

Use this skill to access the Allo audio/video understanding service. The service turns uploaded audio/video into structured evidence before any higher-level reasoning is performed.

The service can provide:

- Media metadata: duration, resolution, codecs, bitrate.
- ASR transcript segments.
- Key frame OCR / screen evidence.
- A unified timeline that aligns transcript, OCR, and frame references.
- LLM summary, chapters, keywords, warnings, and action items.
- Evidence-grounded QA over the processed media.
- Optional translation of extracted evidence.

Default service endpoints:

```text
Base URL: http://221.0.79.252:8090
Demo URL: http://221.0.79.252:8090/demo/
Health:   http://221.0.79.252:8090/health
```

The base URL can be overridden with:

```bash
export AV_UNDERSTANDING_BASE_URL="http://host:port"
```

## When To Use

Use this skill when the user asks to:

- Summarize an audio/video file.
- Analyze a public short-video/share link after downloading the media file, such as a Douyin/TikTok-style share text that contains a URL.
- Transcribe a video or meeting recording.
- Extract OCR / screen text evidence from a video.
- Inspect or continue an existing `job_id`.
- Ask questions about a processed video.
- Produce evidence with timestamps from audio/video content.
- Evaluate, score, or review a recorded presentation, speech, report, or defense (see Workflow D for the default evaluation report).
- Distill what a person says in an interview, podcast, talk, or social video into key claims, quotes, and timestamped takeaways (see Workflow E).
- Build an agent capability around the audio/video understanding API.

Do not use this skill for unrelated general QA, pure local ffmpeg tasks, generic file management, private/non-public videos, or attempts to bypass login, DRM, paywalls, platform access controls, or copyright restrictions. For URL inputs, only download media that is publicly accessible and that the user is allowed to process; if downloading fails, ask the user to provide a local media file.

## Hard Rule: Remote Service Only, No Local Fallback

This skill is only a thin client for the remote Allo service. The remote service is the single source of any reliable result, because only it runs the processed recognition models (ASR, OCR, visual understanding, summarization). Local or offline processing is NOT a valid substitute.

Downloading a user-provided public video URL is only an acquisition step, not analysis. After download, the media must still be uploaded to the remote service and processed through the normal asynchronous job lifecycle. Never use local transcript/OCR/vision models to replace the remote service.

Before doing anything else, you MUST verify service health:

```bash
bash scripts/media_understanding.sh health
```

- If health returns `{"status":"ok"}` (exit `0`): proceed.
- If health does not return ok, the command is unreachable, or it exits non-zero (exit `6`): STOP IMMEDIATELY.

When the service is unhealthy or unreachable, you MUST NOT:

- run any local or offline media analysis (no local `ffmpeg`, `ffprobe`-based analysis beyond duration, `faster-whisper`/`whisper`, local OCR, local pose/vision models, or any other on-device model);
- improvise a substitute pipeline of your own;
- fabricate, estimate, or guess a transcript, timeline, summary, score, or evaluation;
- keep retrying blindly or burn tokens attempting workarounds.

Instead, recall immediately and tell the user plainly:

```text
The audio/video understanding service is currently unavailable (health check failed), so a reliable analysis cannot be produced right now. No local fallback is used because its output would not be trustworthy. Please retry later.
```

The helper script enforces this too: `upload`/`submit`/`analyze` run a mandatory health pre-flight and abort with exit `6` before uploading if the service is down. Treat exit `6` as a hard stop, not an error to work around.

If the service goes down mid-job (exit `4`), the same rule applies: do not switch to local processing. Preserve the `job_id` and resume later when the service is healthy again.

## Core Rule: This Is An Asynchronous Job API

The upload endpoint does not mean the media has been fully processed. Upload only creates a job and returns a `job_id`.

Always follow this lifecycle:

1. Submit media and save `job_id`.
2. Poll `GET /api/jobs/{job_id}` until `status` is `done` or `failed`.
3. Only after `status=done`, fetch timeline, summary, translation, or QA results.
4. If polling stops before completion (service unreachable or explicit timeout), return the `job_id` and current status to the user. Do not upload the same file again unless the user explicitly asks.

For long videos, expect processing to take time because the backend may run media decoding, ASR, OCR, evidence fusion, and LLM summarization.

## Output Hygiene: One Markdown Report By Default

When the user asks for a report, review, evaluation, score, or "process/analyze this presentation", keep the workspace clean.

Default output rules:

- Create at most one user-facing Markdown report (`.md`).
- Do not save `job.json`, `summary.json`, `timeline.json`, `presentation.json`, QA dumps, or other raw JSON files unless the user explicitly asks for raw artifacts.
- Do not run ad hoc local Python scripts just to convert JSON into Markdown. Use the API responses as temporary evidence and write the Markdown report directly.
- If raw JSON is needed for debugging, keep it hidden or temporary and do not present it as the deliverable.
- Preferred filename: `<视频原文件名去扩展名>_处理结果.md`.

The final Markdown should be the deliverable. It may include sections for processing overview, score, dimensions, timeline, highlights, problems, evidence samples, and limitations.

## Polling Policy: Wait Until Done, Gate On Service Health

Processing time cannot be predicted reliably. A short video may still take longer than any estimate. Therefore the default polling policy is:

- Keep polling `GET /api/jobs/{job_id}` until `status` is `done` or `failed`. Do not stop just because an estimated wait time passed.
- If a status query fails, check `GET /health`. If the service is alive, keep polling. The job is still progressing on the server.
- Only give up when the service itself stays unreachable for many consecutive checks (default: 36 checks). Even then, the remote job may still be running, so always preserve the `job_id` and resume later.

Duration-based estimates are soft budgets used only as progress signals, never as hard timeouts:

| Media duration | Soft wait budget (informational) |
| --- | ---: |
| < 5 minutes | 600 seconds |
| 5-15 minutes | 1200 seconds |
| 15-30 minutes | 2400 seconds |
| 30-60 minutes | 3600 seconds |
| > 60 minutes | Do not wait in foreground. Submit, return `job_id`, and resume later. |

When a soft budget is exceeded, the script emits a `soft_budget_exceeded` event and continues polling as long as the service is healthy. A video under 5 minutes that finishes at 610 seconds will still return its real result.

### Desktop / Tool 600s Timeout Resume Rule

Some agent desktop runtimes may kill a single tool call after about 600 seconds even though the backend job is still running. Treat this as a foreground execution timeout only, not as a backend failure.

If a tool call is interrupted by a desktop/runtime timeout before the job reaches `done` or `failed`:

1. Do not re-upload the media.
2. Immediately query the existing job status:

```bash
bash scripts/media_understanding.sh job JOB_ID
```

3. If the job is still `queued`, `processing`, or `running`, start a new polling command with the same job_id:

```bash
bash scripts/media_understanding.sh wait JOB_ID forever 5
```

4. Repeat this resume loop as many times as needed until the backend returns `done` or `failed`.
5. If the job is `done`, fetch `summary`, `timeline`, and `presentation` directly; do not poll again.
6. If the service health is `ok` but job status cannot be read, report that the job_id may be invalid or unavailable; do not upload a duplicate unless the user explicitly asks.

In short: a 600-second tool timeout should become `check job -> continue waiting with same job_id`, never `upload again` and never `local fallback`.

## Helper Script

A helper script is included:

```bash
bash scripts/media_understanding.sh health
bash scripts/media_understanding.sh extract-url "复制打开抖音... https://v.douyin.com/..."
bash scripts/media_understanding.sh download-url "复制打开抖音... https://v.douyin.com/..." /tmp/allo-downloads
bash scripts/media_understanding.sh submit /absolute/path/to/video.mp4
bash scripts/media_understanding.sh analyze /absolute/path/to/video.mp4 auto
bash scripts/media_understanding.sh analyze-url "复制打开抖音... https://v.douyin.com/..." auto /tmp/allo-downloads
bash scripts/media_understanding.sh recommend-wait /absolute/path/to/video.mp4
bash scripts/media_understanding.sh job JOB_ID
bash scripts/media_understanding.sh poll JOB_ID 5 forever
bash scripts/media_understanding.sh wait JOB_ID forever 5
bash scripts/media_understanding.sh timeline JOB_ID
bash scripts/media_understanding.sh summary JOB_ID
bash scripts/media_understanding.sh translation JOB_ID zh-CN
bash scripts/media_understanding.sh presentation JOB_ID [refresh]
bash scripts/media_understanding.sh qa JOB_ID "What is this video mainly about?" 5
bash scripts/media_understanding.sh video-url JOB_ID
```

Environment variables used by the script:

```text
AV_UNDERSTANDING_BASE_URL                default: http://221.0.79.252:8090
AV_UNDERSTANDING_POLL_INTERVAL           default: 5 seconds
AV_UNDERSTANDING_MAX_WAIT_SECONDS        default: forever (poll until done/failed while service is healthy; set a number for a hard timeout)
AV_UNDERSTANDING_MAX_UNREACHABLE_CHECKS  default: 36 consecutive failed liveness checks before giving up
```

URL download behavior:

- Douyin URLs use `scripts/douyin_download.py`, a built-in direct downloader based on Python standard library only. It follows the public share page, extracts SSR router video metadata, resolves the play endpoint, and downloads the returned MP4. This does not require watermark removal.
- Other URL platforms are not supported by this helper. Ask the user to provide a local media file for non-Douyin links.
- If the built-in Douyin path fails, do not invent a transcript or use local analysis. Ask the user to upload/provide the video file directly.

## API Contract

### Health Check

```bash
curl -sS "$AV_UNDERSTANDING_BASE_URL/health"
```

Expected response:

```json
{"status":"ok"}
```

### Submit Media

```bash
curl -sS -X POST "$AV_UNDERSTANDING_BASE_URL/api/videos" \
  -F "file=@/absolute/path/to/media.mp4"
```

Important response fields:

- `job_id`
- `status`
- `filename`
- `message`
- `error`

### Extract And Download Public Video URLs

Use this for pasted social-share text that contains a URL, for example Douyin/TikTok-style copied text.

Extract the first URL:

```bash
bash scripts/media_understanding.sh extract-url "PASTED_SHARE_TEXT"
```

Download public media to a local temporary file:

```bash
bash scripts/media_understanding.sh download-url "PASTED_SHARE_TEXT" /tmp/allo-downloads
```

Download, upload to the remote service, and wait using the normal job lifecycle:

```bash
bash scripts/media_understanding.sh analyze-url "PASTED_SHARE_TEXT" auto /tmp/allo-downloads
```

Important constraints:

- The helper only extracts the URL and acquires the media file; it does not perform local transcription, OCR, summarization, or vision analysis.
- For Douyin share links, the helper uses the direct public-share resolver: short URL -> canonical share/video page -> SSR metadata -> play endpoint -> MP4 file.
- The direct Douyin resolver is best-effort and does not require no-watermark output. Watermarked MP4 is acceptable for downstream understanding.
- The direct Douyin resolver is intentionally implemented as a small script instead of depending on `yt-dlp`, keeping the skill self-contained.
- Only use this for public URLs the user is allowed to process.
- Do not bypass login, private sharing restrictions, DRM, paywalls, or platform access controls.
- If download fails, ask the user to provide the video file directly and then use `analyze /absolute/path/to/file.mp4 auto`.

### Query Job Status

```bash
curl -sS "$AV_UNDERSTANDING_BASE_URL/api/jobs/JOB_ID"
```

Important fields:

- `job_id`
- `status`: typically `queued`, `processing`, `done`, or `failed`
- `message`
- `error`
- `filename`
- `metadata_path`
- `timeline_path`
- `created_at`
- `updated_at`

### Poll Job Until Ready

Use the helper script instead of writing ad hoc polling logic:

```bash
bash scripts/media_understanding.sh poll JOB_ID 5 forever
```

This emits JSON-line progress events such as:

```json
{"event":"poll","elapsed_seconds":10,"job_id":"...","status":"processing","message":"...","error":""}
{"event":"soft_budget_exceeded","elapsed_seconds":620,"soft_budget_seconds":600,"job_id":"...","note":"recommended wait budget exceeded; service is still processing, continuing to poll"}
{"event":"service_unreachable","elapsed_seconds":700,"job_id":"...","consecutive_bad_checks":3,"max_bad_checks":36}
```

Exit behavior:

- exit `0`: job finished with `status=done`
- exit `2`: job failed
- exit `3`: hard wait timeout (only when a numeric max_wait was given); the remote job may still be running
- exit `4`: service stayed unreachable for too many consecutive checks; the remote job may still be running
- exit `5`: service is healthy but job status could not be read (likely an invalid `job_id`)
- exit `6`: service health check failed before submission and the run was aborted. Do not fall back to local/offline processing. Report that the service is unavailable and stop.

On exit `3` or `4`, preserve the `job_id` and resume later with `wait JOB_ID forever 5`. On exit `6`, do not retry locally; report the outage and ask the user to try again later.

### Fetch Timeline Evidence

```bash
curl -sS "$AV_UNDERSTANDING_BASE_URL/api/jobs/JOB_ID/timeline"
```

Important fields:

- `metadata`
- `transcript`
- `timeline`
- frame paths / frame URLs
- OCR text when available

Typical timeline items contain:

- `start`
- `end`
- `frame`
- `frame_url`
- `transcript`
- `ocr`

Use timeline evidence for timestamped answers.

### Fetch Summary

```bash
curl -sS "$AV_UNDERSTANDING_BASE_URL/api/jobs/JOB_ID/summary"
```

Force refresh only when necessary:

```bash
curl -sS "$AV_UNDERSTANDING_BASE_URL/api/jobs/JOB_ID/summary?refresh=true"
```

Important fields:

- `source`
- `summary`
- `chapters`
- `keywords`
- `action_items`
- `warnings`
- `generated_at`


### Fetch Presentation Evaluation

```bash
curl -sS "$AV_UNDERSTANDING_BASE_URL/api/jobs/JOB_ID/presentation-evaluation"
curl -sS "$AV_UNDERSTANDING_BASE_URL/api/jobs/JOB_ID/presentation-evaluation?refresh=true"
```

Helper command:

```bash
bash scripts/media_understanding.sh presentation JOB_ID
bash scripts/media_understanding.sh presentation JOB_ID true
```

Important fields:

- `overall`: evidence-grounded overall evaluation text.
- `score_summary`: content-dimension-only overall score and weights when available.
- `supported_dimensions`: content, logical structure, PPT/screen support, and assignment relevance. These may carry numeric `score` values.
- `evidence_limited_dimensions`: delivery dimensions such as fluency, speech rate, pauses, body language, eye contact, gestures, posture, and confidence; keep unsupported scores as `N/A`.
- `highlights`: each item must include `start`, `end`, `source`, and evidence text. Use these for the report's 闪光点.
- `improvement_points`: each item must include `start`, `end`, `source`, and evidence text. Use these for the report's 问题点/改进点.
- `speech_timing_metrics`: rough ASR-timing-derived stats only, not professional acoustic analysis.
- `warnings`: mandatory caveats.

Use this endpoint for Workflow D whenever available. Do not rely on QA for broad presentation evaluation.

Backward compatibility: if the endpoint exists but does not return `score_summary`, `highlights`, or `improvement_points`, the backend is older than the scoring-report contract. In that case, still produce a report from `supported_dimensions`, citations, summary chapters, and timeline evidence, but explicitly state that numeric overall scoring or timestamped backend highlights were not returned. Do not fabricate those fields.

### Translate Evidence

```bash
curl -sS "$AV_UNDERSTANDING_BASE_URL/api/jobs/JOB_ID/translation?target_language=zh-CN"
```

Translation may be slow. Only call it when the user explicitly needs translated evidence or a translated view.

### Evidence-Grounded QA

```bash
curl -sS -X POST "$AV_UNDERSTANDING_BASE_URL/api/jobs/JOB_ID/qa" \
  -H "Content-Type: application/json" \
  -d '{"question":"What is this video mainly about?","top_k":5}'
```

Important fields:

- `answer`
- `citations`
- `source`
- `warnings`

QA retrieval is currently weak for broad, evaluative, or summary-style questions (for example "evaluate this presentation" or "what is this video about"). In testing it often returns `source=fallback` with empty `citations` even when the summary and timeline clearly contain the answer.

Therefore:

- For broad / evaluative / overview questions, do NOT rely on QA as the primary evidence source. Build the answer directly from `summary` + `timeline` + transcript + OCR + visual evidence, which already carry timestamps.
- Use QA only for narrow, factual lookups ("what number was shown at minute 2", "what tool was mentioned"), and treat it as an optional enhancement on top of the timeline.
- If QA returns no citations or a weak fallback answer, do not invent evidence. Use the summary/timeline evidence and clearly state that QA retrieval did not return direct citations.
- Never upgrade a `source=fallback` answer into a confident claim. A fallback answer is a signal that QA did not match, not a verified result.

### Video Stream And Frames

```text
GET /api/jobs/JOB_ID/video
GET /artifacts/JOB_ID/frames/<frame-file>.jpg
```

These are mainly useful for UI playback and frame preview. Most agent workflows should rely on timeline, summary, and QA endpoints.

## Recommended Workflows

### Workflow A: User Provides A Media File

1. Run the health check first. If it does not return `status=ok` (exit `6`), STOP: do not upload, do not attempt any local/offline processing, and tell the user the service is unavailable and to retry later.
2. Submit the file and capture `job_id`. (The script re-checks health before upload and aborts with exit `6` if the service is down.)
3. Poll the job until `done` or `failed`. Do not stop polling just because an estimated time passed; while `/health` is alive, keep waiting.
4. If done, fetch `timeline` and `summary`.
5. **Decide the output shape from the content, not from the user's wording.** After reading the summary, judge what the recording is. If the summary/OCR/visual evidence shows a single person presenting, reporting, defending, or speaking to an audience (a work review, class presentation, pitch, defense, etc.), switch to Workflow D and produce the evaluation report BY DEFAULT — even if the user only said "process", "analyze", "handle", or "look at" the video and never used an evaluation verb. A general-purpose user will not know to ask for an evaluation; infer it from the content. For other content (meetings, tutorials, screen recordings with no single presenter being assessed), produce the normal summary/timeline output.
6. If polling had to stop (service unreachable, exit `4`), return the `job_id`, current status, and continuation command. Do not produce a local fallback report.
7. Do not re-upload unless explicitly requested.

Example:

```bash
bash scripts/media_understanding.sh analyze /absolute/path/to/video.mp4 auto
```

With `auto`, the helper script tries to detect media duration with `ffprobe`, uses the duration table as a soft budget, and polls until the job is `done` or `failed` while the service stays healthy. Videos longer than 60 minutes are only submitted; the `job_id` is returned without foreground waiting.

If polling had to stop early, respond with:

```text
The media job has been submitted and is still processing. job_id=JOB_ID. Continue later with: bash scripts/media_understanding.sh wait JOB_ID forever 5
```

### Workflow B: User Provides An Existing job_id

1. Query `job JOB_ID`.
2. If `status=done`, fetch `summary` and `timeline`; for presentation/speech content, also fetch `presentation-evaluation`.
3. If `status=failed`, report the backend error.
4. If still processing, poll if appropriate; otherwise return the current status and ask the user to continue later.

### Workflow C: User Asks A Question About A Processed Video

1. Ensure the job is `done`.
2. Call `qa JOB_ID "question" 5`.
3. If citations are present, answer with citations and timestamps.
4. If QA is weak or empty, use `summary` and `timeline` as fallback evidence.

### Workflow E: Public Social Video URL / Speech Distillation

Use this workflow when the user provides pasted social-video share text or a URL and asks to distill what the person says, summarize an interview, extract观点, or turn a public video into key takeaways. This is a separate branch from presentation scoring: it should not force a presentation evaluation unless the content is clearly a presentation/report or the user asks for scoring.

1. Run health check first. If the service is unhealthy, stop; do not download or analyze locally.
2. Extract the URL from the pasted text:

```bash
bash scripts/media_understanding.sh extract-url "PASTED_SHARE_TEXT"
```

3. Download the public media file with the helper:

```bash
bash scripts/media_understanding.sh download-url "PASTED_SHARE_TEXT" /tmp/allo-downloads
```

4. Submit the downloaded file to the remote service and poll until `done`:

```bash
bash scripts/media_understanding.sh analyze /absolute/path/to/downloaded.mp4 auto
```

   Or use the combined helper:

```bash
bash scripts/media_understanding.sh analyze-url "PASTED_SHARE_TEXT" auto /tmp/allo-downloads
```

5. Fetch `summary` and `timeline`. Use QA only for narrow follow-up questions; broad distillation should be built from summary/timeline/ASR evidence.
6. Produce a concise speech-distillation Markdown report by default. Recommended sections:
   - `视频信息`: job_id, filename, duration, evidence channels.
   - `一句话结论`: what the speaker mainly says.
   - `核心观点`: 3-8 key claims, each with timestamp evidence when possible.
   - `论据/例子`: supporting examples or stories from the speaker.
   - `值得引用的原话`: short quotes only when ASR is clear enough; include timestamps and disclose ASR caveats.
   - `时间线`: chapter-style structure.
   - `可复用内容`: bullets suitable for notes, research, or content planning.
   - `限制说明`: ASR/OCR/visual quality caveats.

Hard boundaries:

- This workflow distills the speaker's content; it does not judge body language or presentation performance unless the user asks and evidence supports it.
- Do not fabricate exact quotes when ASR is noisy. Use paraphrases and mark them as summaries.
- Do not download private or restricted videos. If URL acquisition fails, ask for a local file.
- Do not create many intermediate files. Keep the downloaded media only as the acquisition artifact; final user-facing output should remain one Markdown report unless raw artifacts are requested.

### Workflow D: Presentation / Speech Quality Evaluation (Default Report)

Trigger this workflow based on WHAT THE VIDEO IS, not on the exact words the user used. If the processed media is a recording of a person presenting, reporting, defending, lecturing, or speaking to an audience (for example a work review, a class presentation, a pitch, a thesis defense, a speech), produce the structured evaluation report below BY DEFAULT.

This is important: most users will NOT phrase a precise request. A plain instruction like "process this video", "analyze this video", "take a look at this presentation", or just handing over the file is ENOUGH to produce the evaluation report, as long as the content is clearly someone presenting. Do not wait for the words "evaluate", "score", or "review". A presentation recording defaults to an evaluation report.

How to tell it is a presentation (use the returned evidence, after the job is `done`): the summary/visual describe a single person speaking to an audience or camera, OCR shows slide-like pages, and the structure looks like a talk (opening, body, closing). When in doubt and the content is plausibly someone presenting, prefer the evaluation report.

Exceptions, where you should NOT force the evaluation report:
- The user explicitly asks for something else (just a transcript, just a summary, a specific question, a translation). Follow the user's explicit request.
- The media is clearly not a person presenting (a tutorial screencast with no presenter, a movie clip, music, ambient footage, a meeting with no single presenter). Use the normal workflow (A/B/C) instead.
- The user gives their own format, rubric, or constraints. Follow the user instead.

This workflow runs on top of the normal lifecycle (health check, submit, poll until `done`, then fetch `timeline` and `summary`). It never bypasses the Hard Rule or the Evidence Grounding Rules. The whole point of the report is to be traceable and non-hallucinated: every point ties back to a returned timestamp and evidence type, and dimensions without supporting evidence are labeled as such instead of guessed.

Default report structure:

1. **处理概览**: job_id, status=done, duration, resolution, and available evidence channels (ASR segment count, timeline entry count, OCR count, visual count, summary source, presentation-evaluation source). This makes the evidence base auditable.
2. **综合评价与分数**: use `presentation-evaluation.overall` and `score_summary` when available. The overall score must be labeled **内容维度综合分**, not full delivery-performance score, unless the backend explicitly says otherwise.
3. **维度评分表**: supported dimensions with `score` / `level` / comment / evidence. Use backend `supported_dimensions[].score` when present. For unsupported delivery dimensions, write `N/A（证据有限）`, never invent a score.
4. **可追溯章节结构**: chapter breakdown from summary with time ranges.
5. **闪光点 highlights**: every item MUST carry `[mm:ss-mm:ss][source]`. Prefer backend `highlights`; otherwise derive only from `supported_dimensions[].citations`, summary chapters, or timeline items. No timestamp/source = do not include the highlight.
6. **问题点 / 改进点 improvement_points**: every item MUST carry `[mm:ss-mm:ss][source]`. Prefer backend `improvement_points`; otherwise derive only from explicit warnings, evidence limitations, noisy OCR samples, or timestamped visual-behavior limitations. No timestamp/source = do not include the point.
7. **客观证据样例**: a small table of `time range | evidence type | evidence content | conclusion it supports`.
8. **限制说明**: ASR/OCR/visual warnings, sampled-frame coverage, and unsupported behavior/acoustic scoring caveats.


Report hard gates for Workflow D:

- Fetch `presentation-evaluation` in addition to `summary` and `timeline` when the endpoint is available.
- If `score_summary.available=true`, show `score_summary.overall_score` as `内容维度综合分` and preserve the note that delivery dimensions are excluded.
- If `score_summary` is missing but supported dimension scores exist, show the dimension scores and state that the backend did not return an overall content score. Do not calculate your own weighted overall score unless the backend provides `score_summary`.
- If neither `score_summary` nor supported dimension scores exist, do not invent numeric scores. Produce a qualitative evidence-grounded report instead.
- Every highlight/problem/improvement bullet must contain `[mm:ss-mm:ss][source]`. If you cannot attach a returned timestamp and source, remove the bullet.
- Prefer backend `highlights` and `improvement_points`; they are designed to be timestamped. If they are absent, derive from existing citations only.
- Keep final user-facing output clean: one Markdown report by default. Raw JSON may be kept only in a hidden/debug location or when the user asks for it.

Recommended Markdown skeleton:

```markdown
# <视频名> 处理结果

## 1. 处理概览
## 2. 综合评价与内容维度综合分
## 3. 维度评分表
## 4. 可追溯章节结构
## 5. 闪光点（必须带时间戳）
## 6. 问题点与改进建议（必须带时间戳）
## 7. 客观证据样例
## 8. 限制说明
```

Mandatory honesty constraints for this report (these are what make it "with evidence, not hallucinated"):

- Dimensions that depend on behavioral or acoustic signals (body language, gestures, posture, eye contact, speaking rate, pauses, filler words) may be only partially supported. Even if the backend returns lightweight visual-behavior or ASR-timing fields, treat them as approximate evidence. Do NOT emit a precise behavioral score unless the backend explicitly returns one; otherwise mark these dimensions as `N/A` / `evidence_limited` and give cautious qualitative remarks.
- If the summary `warnings` mention ASR being broken/garbled, disclose it and do not score "fluency" down purely because the transcript looks broken.
- If the timeline is sparse for parts of the video, state that coverage was limited there rather than implying full-frame analysis.
- Do not claim per-frame analysis. The service returns key-frame / sampled visual evidence, not every frame.

In short: score what the evidence supports, timestamp every claim, and explicitly flag every dimension the current evidence cannot back. A report that honestly says "body-language scoring is not supported by the available evidence" is correct; a confident gesture/eye-contact score invented from a prose caption is a violation.

## Answering Guidelines

Prefer concise outputs with evidence:

1. Short answer or conclusion.
2. Key supporting timestamps.
3. Relevant ASR/OCR evidence.
4. Caveats, especially if QA returned fallback or citations are missing.

When possible, cite time ranges:

```text
At 00:54-01:16, the speaker discusses RAG, Tool, MCP, and Skill as core Agent components.
```

Do not claim that the video was fully analyzed unless `GET /api/jobs/{job_id}` returned `status=done`.

Do not discard a `job_id` after timeout. The `job_id` is the durable handle for resuming the task.

## Evidence Grounding Rules (Anti-Hallucination)

These rules apply to every claim, score, highlight, or problem you report. They exist because the agent must never present a guess as a verified observation.

### Every claim must be backed by returned evidence

- Each highlight, issue, or score MUST point to concrete evidence that the service actually returned: a timestamp plus the evidence type (`asr`, `ocr`, `visual`, `summary`). Do not state a conclusion that you cannot tie to a returned field.
- If you cannot find supporting evidence in the timeline/summary for a claim, do not make the claim.

### Only judge what the evidence actually supports

The service returns ASR transcript, OCR text, frame-level visual descriptions, a timeline, a summary, and may return presentation-evaluation fields such as lightweight visual-behavior observations and rough ASR-timing metrics. These are still not the same as professional pose/gaze/acoustic analysis.

Therefore:

- You MAY assess content structure, topic coverage, on-screen text, and what is visibly happening, because these are directly supported.
- You MUST NOT produce precise scores for body language, eye contact, gesture richness, posture, fluency, speaking rate, or filler-word usage from prose visual descriptions alone. A visual caption like "a person holding a microphone on stage" does not support a gesture or eye-contact score. If the backend marks a delivery dimension as `evidence_limited`, keep its score as `N/A`.
- When a requested dimension lacks structured evidence, say so explicitly: state that the evidence is insufficient and give only a conservative, clearly-labeled qualitative note instead of a precise number. Never invent a metric to fill the gap.

### Using transcript timing for fluency (only as far as it goes)

Timeline transcript segments carry `start`/`end`. You may use the gaps between consecutive segments as a rough signal of pauses, and segment text length over time as a rough pace signal. Treat these as approximate, and explicitly flag that they are derived from ASR timing, not from a dedicated speech-analysis model.

Be aware ASR quality degrades on accented or noisy audio: repeated tokens or garbled words in the transcript (and `warnings` in the summary) may reflect recognition errors, not the speaker's actual delivery. Do not score fluency down purely because the transcript looks broken; note the ASR-quality caveat instead.

### Be honest about coverage

On long videos the timeline can be sparse (for example only a handful of OCR/visual entries across several minutes). When evidence is sparse for a section, say the coverage is limited rather than implying the whole video was densely analyzed.

## Failure Handling

- Health check fails (exit `6`): the service is unavailable. Recall immediately. Report the service as unavailable and DO NOT upload, DO NOT fall back to any local/offline processing, and DO NOT fabricate results. See the Hard Rule above.
- Upload fails: report server response and do not retry blindly. Do not switch to local processing.
- Polling stopped early (hard timeout exit `3` or service unreachable exit `4`): report not-ready state, keep `job_id`, and provide a continuation command (`wait JOB_ID forever 5`). Do not switch to local processing.
- Job failed (exit `2`): report backend `error` and `message`.
- Summary/timeline unavailable while job is not done: poll or tell the user the job is still processing.
- QA returns no citations: use summary/timeline fallback and disclose that QA retrieval did not find direct matches. (This is evidence fallback within the same job, not local processing.)

## Minimal Smoke Test

Use this known job ID only for testing connectivity if still present on the server:

```bash
bash scripts/media_understanding.sh job 7aa8c86b-d887-487d-84d6-387e79368db9
bash scripts/media_understanding.sh summary 7aa8c86b-d887-487d-84d6-387e79368db9
```
