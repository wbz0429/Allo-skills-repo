#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${AV_UNDERSTANDING_BASE_URL:-http://221.0.79.252:8090}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
DEFAULT_POLL_INTERVAL="${AV_UNDERSTANDING_POLL_INTERVAL:-5}"
# "forever" means: keep polling until the job is done/failed, as long as the
# service health check stays alive. A number means a hard foreground timeout.
DEFAULT_MAX_WAIT_SECONDS="${AV_UNDERSTANDING_MAX_WAIT_SECONDS:-forever}"
# How many consecutive failed liveness checks before we give up polling.
MAX_UNREACHABLE_CHECKS="${AV_UNDERSTANDING_MAX_UNREACHABLE_CHECKS:-36}"

usage() {
  cat <<USAGE
Usage:
  $0 health
  $0 extract-url "pasted share text or URL"
  $0 download-url "pasted share text or URL" [output_dir]
  $0 upload /absolute/path/to/file.mp4
  $0 submit /absolute/path/to/file.mp4
  $0 analyze /absolute/path/to/file.mp4 [max_wait_sec|auto|forever]
  $0 analyze-url "pasted share text or URL" [max_wait_sec|auto|forever] [output_dir]
  $0 recommend-wait /absolute/path/to/file.mp4
  $0 job JOB_ID
  $0 poll JOB_ID [interval_seconds] [max_wait_seconds|forever]
  $0 wait JOB_ID [max_wait_seconds|forever] [interval_seconds]
  $0 timeline JOB_ID
  $0 summary JOB_ID [refresh]
  $0 translation JOB_ID [target_language] [refresh]
  $0 presentation JOB_ID [refresh]
  $0 qa JOB_ID "question text" [top_k]
  $0 video-url JOB_ID

Environment:
  AV_UNDERSTANDING_BASE_URL                default: http://221.0.79.252:8090
  AV_UNDERSTANDING_POLL_INTERVAL           default: 5 seconds
  AV_UNDERSTANDING_MAX_WAIT_SECONDS        default: forever (poll until done/failed while service is healthy)
  AV_UNDERSTANDING_MAX_UNREACHABLE_CHECKS  default: 36 (consecutive failed liveness checks before giving up)

Exit codes:
  0  ok / job done
  2  job failed (backend error)
  3  hard wait timeout (only when a numeric max_wait was given); job may still be running
  4  service unreachable for too many consecutive checks; job may still be running
  5  service is healthy but job status could not be read (likely bad job_id)
  6  service health check failed BEFORE submission; aborted. DO NOT fall back to
     local/offline processing. No reliable result is possible while the remote
     service is down.
  7  URL download failed or no supported downloader was found. Ask the user to
     provide a local media file instead; do not perform local/offline analysis.
USAGE
}

need_arg() {
  local name="$1"
  local value="${2:-}"
  if [ -z "$value" ]; then
    echo "missing argument: $name" >&2
    usage >&2
    exit 1
  fi
}

json_get() {
  local key="$1"
  python3 -c 'import json,sys; obj=json.load(sys.stdin); print(obj.get(sys.argv[1], ""))' "$key"
}

json_event() {
  python3 - "$@" <<'PY'
import json, sys
it = iter(sys.argv[1:])
out = {}
for key, value in zip(it, it):
    if value == "__NONE__":
        out[key] = None
    else:
        try:
            out[key] = int(value)
        except ValueError:
            out[key] = value
print(json.dumps(out, ensure_ascii=False))
PY
}

media_duration_seconds() {
  local file="$1"
  if ! command -v ffprobe >/dev/null 2>&1; then
    return 1
  fi
  ffprobe -v error -show_entries format=duration -of default=nk=1:nw=1 "$file" 2>/dev/null \
    | python3 -c 'import sys, math; s=sys.stdin.read().strip(); print(int(math.ceil(float(s)))) if s else sys.exit(1)' 2>/dev/null
}

recommended_wait_seconds() {
  local duration="$1"
  python3 - "$duration" <<'PY'
import sys
d = int(float(sys.argv[1]))
if d < 5 * 60:
    print(600)
elif d <= 15 * 60:
    print(1200)
elif d <= 30 * 60:
    print(2400)
elif d <= 60 * 60:
    print(3600)
else:
    print(0)
PY
}

extract_first_url() {
  local text="$1"
  python3 - "$text" <<'PY'
import re
import sys

text = sys.argv[1]
match = re.search(r"https?://[^\s，。；、)）\]】>]+", text)
if not match:
    sys.exit(1)
print(match.group(0))
PY
}

is_douyin_url() {
  local url="$1"
  case "$url" in
    *douyin.com*|*iesdouyin.com*|*snssdk.com*) return 0 ;;
    *) return 1 ;;
  esac
}

download_douyin_media() {
  local url="$1"
  local output_dir="$2"
  python3 "$SCRIPT_DIR/douyin_download.py" "$url" -o "$output_dir"
}

download_url_media() {
  local input="$1"
  local output_dir="${2:-${TMPDIR:-/tmp}/allo-av-understanding-downloads}"
  need_arg url_or_share_text "$input"
  require_healthy

  local url=""
  if ! url="$(extract_first_url "$input")"; then
    echo "no URL found in input" >&2
    return 1
  fi

  if is_douyin_url "$url"; then
    if downloaded="$(download_douyin_media "$url" "$output_dir")"; then
      printf '%s\n' "$downloaded"
      return 0
    fi
    echo "direct Douyin downloader failed. Ask the user to provide a local video file." >&2
    return 7
  fi

  echo "only Douyin public share URLs are supported by download-url/analyze-url. Provide a local media file for other platforms." >&2
  return 7
}

analyze_file() {
  local file="$1"
  local max_wait_arg="${2:-auto}"
  need_arg file "$file"

  local duration=""
  local soft_budget=""
  local max_wait="forever"
  local wait_policy="forever"
  if [ "$max_wait_arg" = "auto" ]; then
    if duration="$(media_duration_seconds "$file")"; then
      soft_budget="$(recommended_wait_seconds "$duration")"
      wait_policy="forever_with_soft_budget"
    fi
  elif [ "$max_wait_arg" = "forever" ]; then
    wait_policy="forever"
  else
    max_wait="$max_wait_arg"
    wait_policy="explicit_hard_timeout"
  fi

  result="$(upload_file "$file")"
  printf '%s\n' "$result"
  job_id="$(printf '%s' "$result" | json_get job_id)"
  if [ -z "$job_id" ]; then
    echo "upload did not return job_id" >&2
    exit 1
  fi

  if [ "$soft_budget" = "0" ]; then
    json_event event submitted_long_video job_id "$job_id" duration_seconds "${duration:-__NONE__}" hint "Video is longer than 60 minutes; do not wait in foreground. Resume later with: bash scripts/media_understanding.sh wait $job_id forever 10"
    exit 0
  fi

  json_event event wait_budget job_id "$job_id" wait_policy "$wait_policy" duration_seconds "${duration:-__NONE__}" soft_budget_seconds "${soft_budget:-__NONE__}" max_wait "$max_wait" poll_interval_seconds "$DEFAULT_POLL_INTERVAL"
  if poll_job "$job_id" "$DEFAULT_POLL_INTERVAL" "$max_wait" "$soft_budget"; then
    json_event event ready next "summary,timeline,qa" job_id "$job_id"
    curl -sS --connect-timeout 10 --max-time 120 "$BASE_URL/api/jobs/$job_id/summary"
  else
    code=$?
    json_event event not_ready job_id "$job_id" exit_code "$code" hint "Resume with: bash scripts/media_understanding.sh wait $job_id forever 5 or check status with: bash scripts/media_understanding.sh job $job_id"
    exit "$code"
  fi
}

service_alive() {
  curl -sS --connect-timeout 5 --max-time 15 "$BASE_URL/health" >/dev/null 2>&1
}

# Mandatory pre-flight gate. The remote service is the only source of reliable
# results. If /health does not report ok, abort with exit 6 instead of letting
# any caller continue. This is the recall switch: never proceed to a degraded
# path when the backend is down.
require_healthy() {
  local body=""
  body="$(curl -sS --connect-timeout 5 --max-time 15 "$BASE_URL/health" 2>/dev/null || true)"
  local ok=""
  if [ -n "$body" ]; then
    ok="$(printf '%s' "$body" | python3 -c 'import json,sys
try:
    print("ok" if json.load(sys.stdin).get("status") == "ok" else "")
except Exception:
    print("")' 2>/dev/null || true)"
  fi
  if [ "$ok" != "ok" ]; then
    json_event event service_health_gate_failed base_url "$BASE_URL" health_body "${body:-__NONE__}" action "abort" note "Remote service health check did not return status=ok. Aborting. DO NOT fall back to local or offline processing; any local result would be unreliable and waste tokens. Tell the user the service is unavailable and to retry later."
    echo "service health check failed; aborting before submission. Do not fall back to local processing." >&2
    exit 6
  fi
}

upload_file() {
  local file="$1"
  need_arg file "$file"
  if [ ! -f "$file" ]; then
    echo "file not found: $file" >&2
    exit 1
  fi
  require_healthy
  curl -sS --connect-timeout 10 --max-time 300 -X POST "$BASE_URL/api/videos" -F "file=@$file"
}

# poll_job JOB_ID [interval] [max_wait|forever] [soft_budget_seconds]
#
# max_wait = "forever": never stop on time alone. Keep polling until the job
# is done/failed, as long as the service liveness check keeps passing.
# soft_budget: optional informational budget; when exceeded a one-time
# soft_budget_exceeded event is emitted but polling continues.
poll_job() {
  local job_id="$1"
  local interval="${2:-$DEFAULT_POLL_INTERVAL}"
  local max_wait="${3:-$DEFAULT_MAX_WAIT_SECONDS}"
  local soft_budget="${4:-}"
  need_arg job_id "$job_id"

  local start now elapsed json status error
  local bad_checks=0 soft_notified=0
  start="$(date +%s)"
  while true; do
    json="$(curl -sS --connect-timeout 10 --max-time 60 "$BASE_URL/api/jobs/$job_id" 2>/dev/null || true)"
    status=""
    if [ -n "$json" ]; then
      status="$(printf '%s' "$json" | json_get status 2>/dev/null || true)"
    fi
    now="$(date +%s)"
    elapsed=$((now - start))

    if [ -n "$status" ]; then
      bad_checks=0
      error="$(printf '%s' "$json" | json_get error 2>/dev/null || true)"
      python3 -c 'import json,sys; obj=json.loads(sys.argv[1]); print(json.dumps({"event":"poll","elapsed_seconds":int(sys.argv[2]),"job_id":obj.get("job_id"),"status":obj.get("status"),"message":obj.get("message",""),"error":obj.get("error","")}, ensure_ascii=False))' "$json" "$elapsed"

      if [ "$status" = "done" ]; then
        return 0
      fi
      if [ "$status" = "failed" ]; then
        echo "job failed: $error" >&2
        return 2
      fi
    else
      # Job status query failed or returned unparsable data.
      # Decide via /health whether the service is alive before giving up.
      if service_alive; then
        bad_checks=$((bad_checks + 1))
        json_event event job_query_degraded elapsed_seconds "$elapsed" job_id "$job_id" consecutive_bad_checks "$bad_checks" max_bad_checks "$MAX_UNREACHABLE_CHECKS" note "job status query failed but /health is ok; continuing to poll"
        if [ "$bad_checks" -ge "$MAX_UNREACHABLE_CHECKS" ]; then
          echo "service is healthy but job status for $job_id could not be read after $bad_checks consecutive checks; verify the job_id" >&2
          return 5
        fi
      else
        bad_checks=$((bad_checks + 1))
        json_event event service_unreachable elapsed_seconds "$elapsed" job_id "$job_id" consecutive_bad_checks "$bad_checks" max_bad_checks "$MAX_UNREACHABLE_CHECKS"
        if [ "$bad_checks" -ge "$MAX_UNREACHABLE_CHECKS" ]; then
          echo "service unreachable after $bad_checks consecutive checks; the remote job may still be running. Save job_id and retry later: $job_id" >&2
          return 4
        fi
      fi
    fi

    if [ -n "$soft_budget" ] && [ "$soft_notified" = "0" ] && [ "$elapsed" -ge "$soft_budget" ]; then
      soft_notified=1
      json_event event soft_budget_exceeded elapsed_seconds "$elapsed" soft_budget_seconds "$soft_budget" job_id "$job_id" note "recommended wait budget exceeded; service is still processing, continuing to poll"
    fi

    if [ "$max_wait" != "forever" ] && [ "$elapsed" -ge "$max_wait" ]; then
      echo "wait timeout after ${elapsed}s; job is still '${status:-unknown}'. Save job_id and check later: $job_id" >&2
      return 3
    fi

    sleep "$interval"
  done
}

cmd="${1:-}"
case "$cmd" in
  health)
    # Print the raw health response, then gate on it. Exit 6 when the service
    # is not healthy so callers can treat "not ok" as a hard stop and never
    # fall back to local/offline processing.
    body="$(curl -sS --connect-timeout 10 --max-time 30 "$BASE_URL/health" 2>/dev/null || true)"
    printf '%s\n' "${body:-}"
    ok=""
    if [ -n "$body" ]; then
      ok="$(printf '%s' "$body" | python3 -c 'import json,sys
try:
    print("ok" if json.load(sys.stdin).get("status") == "ok" else "")
except Exception:
    print("")' 2>/dev/null || true)"
    fi
    if [ "$ok" != "ok" ]; then
      echo "health check failed; service is unavailable. Do not fall back to local processing." >&2
      exit 6
    fi
    ;;
  extract-url)
    input="${2:-}"
    need_arg url_or_share_text "$input"
    extract_first_url "$input"
    ;;
  download-url)
    input="${2:-}"
    output_dir="${3:-${TMPDIR:-/tmp}/allo-av-understanding-downloads}"
    download_url_media "$input" "$output_dir"
    ;;
  upload|submit)
    file="${2:-}"
    upload_file "$file"
    ;;
  recommend-wait)
    file="${2:-}"
    need_arg file "$file"
    if [ ! -f "$file" ]; then
      echo "file not found: $file" >&2
      exit 1
    fi
    if duration="$(media_duration_seconds "$file")"; then
      wait_seconds="$(recommended_wait_seconds "$duration")"
      json_event event recommend_wait duration_seconds "$duration" recommended_wait_seconds "$wait_seconds" note "soft budget only; polling continues past it while the service is healthy"
    else
      json_event event recommend_wait duration_seconds __NONE__ recommended_wait_seconds __NONE__ warning "ffprobe unavailable or duration not detected; no soft budget"
    fi
    ;;
  analyze)
    file="${2:-}"
    max_wait_arg="${3:-auto}"
    analyze_file "$file" "$max_wait_arg"
    ;;
  analyze-url)
    input="${2:-}"
    max_wait_arg="${3:-auto}"
    output_dir="${4:-${TMPDIR:-/tmp}/allo-av-understanding-downloads}"
    file="$(download_url_media "$input" "$output_dir")"
    json_event event downloaded_media file "$file"
    analyze_file "$file" "$max_wait_arg"
    ;;
  job)
    job_id="${2:-}"
    need_arg job_id "$job_id"
    curl -sS --connect-timeout 10 --max-time 60 "$BASE_URL/api/jobs/$job_id"
    ;;
  poll)
    job_id="${2:-}"
    interval="${3:-$DEFAULT_POLL_INTERVAL}"
    max_wait="${4:-$DEFAULT_MAX_WAIT_SECONDS}"
    poll_job "$job_id" "$interval" "$max_wait"
    ;;
  wait)
    job_id="${2:-}"
    max_wait="${3:-$DEFAULT_MAX_WAIT_SECONDS}"
    interval="${4:-$DEFAULT_POLL_INTERVAL}"
    poll_job "$job_id" "$interval" "$max_wait"
    ;;
  timeline)
    job_id="${2:-}"
    need_arg job_id "$job_id"
    curl -sS --connect-timeout 10 --max-time 120 "$BASE_URL/api/jobs/$job_id/timeline"
    ;;
  summary)
    job_id="${2:-}"
    refresh="${3:-false}"
    need_arg job_id "$job_id"
    if [ "$refresh" = "true" ]; then
      curl -sS --connect-timeout 10 --max-time 180 "$BASE_URL/api/jobs/$job_id/summary?refresh=true"
    else
      curl -sS --connect-timeout 10 --max-time 120 "$BASE_URL/api/jobs/$job_id/summary"
    fi
    ;;
  translation)
    job_id="${2:-}"
    target_language="${3:-zh-CN}"
    refresh="${4:-false}"
    need_arg job_id "$job_id"
    if [ "$refresh" = "true" ]; then
      curl -sS --connect-timeout 10 --max-time 300 "$BASE_URL/api/jobs/$job_id/translation?target_language=$target_language&refresh=true"
    else
      curl -sS --connect-timeout 10 --max-time 300 "$BASE_URL/api/jobs/$job_id/translation?target_language=$target_language"
    fi
    ;;
  presentation)
    job_id="${2:-}"
    refresh="${3:-false}"
    need_arg job_id "$job_id"
    if [ "$refresh" = "true" ]; then
      curl -sS --connect-timeout 10 --max-time 180 "$BASE_URL/api/jobs/$job_id/presentation-evaluation?refresh=true"
    else
      curl -sS --connect-timeout 10 --max-time 120 "$BASE_URL/api/jobs/$job_id/presentation-evaluation"
    fi
    ;;
  qa)
    job_id="${2:-}"
    question="${3:-}"
    top_k="${4:-5}"
    need_arg job_id "$job_id"
    need_arg question "$question"
    payload="$(QUESTION="$question" TOP_K="$top_k" python3 - <<'PY'
import json, os
print(json.dumps({"question": os.environ["QUESTION"], "top_k": int(os.environ["TOP_K"])}, ensure_ascii=False))
PY
)"
    curl -sS --connect-timeout 10 --max-time 180 -X POST "$BASE_URL/api/jobs/$job_id/qa" \
      -H 'Content-Type: application/json' \
      -d "$payload"
    ;;
  video-url)
    job_id="${2:-}"
    need_arg job_id "$job_id"
    printf '%s\n' "$BASE_URL/api/jobs/$job_id/video"
    ;;
  *)
    usage >&2
    exit 1
    ;;
esac
