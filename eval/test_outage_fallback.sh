#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${PROJECT_ROOT}/docker-compose.yml"
COMPOSE=(docker compose --project-directory "${PROJECT_ROOT}" -f "${COMPOSE_FILE}")
API_BASE_URL="${API_BASE_URL:-http://localhost:80}"
PROXY_BASE_URL="${PROXY_BASE_URL:-http://localhost:4000}"
OUTAGE_SECONDS="${OUTAGE_SECONDS:-60}"
HEALTH_TIMEOUT_SECONDS="${HEALTH_TIMEOUT_SECONDS:-90}"
REQUEST_TIMEOUT_SECONDS="${REQUEST_TIMEOUT_SECONDS:-5}"
PYTHON="${PYTHON:-python}"
TEMP_DIR=""
PROXY_MAY_BE_STOPPED=0
FAILURES=0

pass() {
  printf 'PASS: %s\n' "$1"
}

fail() {
  printf 'FAIL: %s\n' "$1" >&2
}

fatal() {
  fail "$1"
  exit 1
}

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM HUP

  if [[ "${PROXY_MAY_BE_STOPPED}" -eq 1 ]]; then
    printf '\nSafety cleanup: restarting local litellm-proxy...\n' >&2
    if "${COMPOSE[@]}" start litellm-proxy; then
      if wait_for_proxy; then
        pass "Cleanup restarted litellm-proxy and its readiness endpoint responded."
      else
        fail "Cleanup started litellm-proxy, but readiness was not confirmed."
        exit_code=1
      fi
    else
      fail "Cleanup could not restart litellm-proxy. Start it manually with: docker compose -f \"${COMPOSE_FILE}\" start litellm-proxy"
      exit_code=1
    fi
    PROXY_MAY_BE_STOPPED=0
  fi

  if [[ -n "${TEMP_DIR}" && -d "${TEMP_DIR}" ]]; then
    rm -rf -- "${TEMP_DIR}"
  fi
  exit "${exit_code}"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

wait_for_proxy() {
  "${PYTHON}" - "${PROXY_BASE_URL}" "${HEALTH_TIMEOUT_SECONDS}" <<'PY'
import sys
import time
import urllib.error
import urllib.request

base_url = sys.argv[1].rstrip("/")
timeout_seconds = float(sys.argv[2])
deadline = time.monotonic() + timeout_seconds
paths = ("/health/readiness", "/health/liveliness")
last_error = "no readiness endpoint responded"

while time.monotonic() < deadline:
    for path in paths:
        try:
            request = urllib.request.Request(
                base_url + path,
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=1.5) as response:
                body = response.read().decode("utf-8", errors="replace")
                if 200 <= response.status < 300:
                    print(f"Proxy readiness succeeded at {path}: HTTP {response.status}")
                    raise SystemExit(0)
                last_error = f"{path} returned HTTP {response.status}: {body[:200]}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = f"{path}: {exc}"
    time.sleep(1)

print(f"Proxy readiness did not succeed within {timeout_seconds:g}s: {last_error}", file=sys.stderr)
raise SystemExit(1)
PY
}

if [[ ! -f "${COMPOSE_FILE}" ]]; then
  fatal "Expected project Compose file not found: ${COMPOSE_FILE}"
fi

if ! command -v docker >/dev/null 2>&1; then
  fatal "Docker CLI is not installed or is not on PATH."
fi
if ! command -v "${PYTHON}" >/dev/null 2>&1; then
  fatal "Python executable '${PYTHON}' was not found. Set PYTHON to a working Python 3 executable."
fi

if ! "${PYTHON}" - "${API_BASE_URL}" "${PROXY_BASE_URL}" <<'PY'
import ipaddress
import sys
from urllib.parse import urlsplit

for label, raw_url in (("API_BASE_URL", sys.argv[1]), ("PROXY_BASE_URL", sys.argv[2])):
    try:
        parsed = urlsplit(raw_url)
        hostname = parsed.hostname
        if parsed.scheme not in ("http", "https") or not hostname:
            raise ValueError("must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("credentials in URLs are not permitted")
        if parsed.query or parsed.fragment:
            raise ValueError("query strings and fragments are not permitted")
        try:
            is_local = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_local = hostname.casefold() == "localhost"
        if not is_local:
            raise ValueError("host must be localhost or a loopback IP address")
    except ValueError as exc:
        print(f"Refusing non-local {label}={raw_url!r}: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print(f"PASS: {label} targets local host {hostname}.")
PY
then
  fatal "API and proxy endpoints must both point to the local test machine."
fi

if ! [[ "${OUTAGE_SECONDS}" =~ ^[0-9]+$ ]] || [[ "${OUTAGE_SECONDS}" -lt 1 ]]; then
  fatal "OUTAGE_SECONDS must be an integer of at least 1."
fi
if ! [[ "${HEALTH_TIMEOUT_SECONDS}" =~ ^[0-9]+$ ]] || [[ "${HEALTH_TIMEOUT_SECONDS}" -lt 1 ]]; then
  fatal "HEALTH_TIMEOUT_SECONDS must be an integer of at least 1."
fi
if ! [[ "${REQUEST_TIMEOUT_SECONDS}" =~ ^[0-9]+$ ]] || [[ "${REQUEST_TIMEOUT_SECONDS}" -lt 1 ]]; then
  fatal "REQUEST_TIMEOUT_SECONDS must be an integer of at least 1."
fi

if [[ -n "${DOCKER_HOST:-}" ]]; then
  case "${DOCKER_HOST}" in
    unix://*|npipe://*) ;;
    *) fatal "Refusing to run: DOCKER_HOST is not a local Unix socket or Windows named pipe: ${DOCKER_HOST}" ;;
  esac
fi

if ! docker info >/dev/null 2>&1; then
  fatal "Docker daemon is unavailable. Start the intended local Docker Desktop/Engine first."
fi

docker_context="$(docker context show 2>/dev/null)" ||
  fatal "Could not determine the active Docker context."
context_endpoint="$(docker context inspect "${docker_context}" --format '{{(index .Endpoints "docker").Host}}' 2>/dev/null)" ||
  fatal "Could not inspect active Docker context '${docker_context}'."
case "${context_endpoint}" in
  unix://*|npipe://*) ;;
  *) fatal "Refusing to run against non-local Docker context '${docker_context}' (${context_endpoint})." ;;
esac
pass "Docker context '${docker_context}' uses local endpoint '${context_endpoint}'."

if ! "${COMPOSE[@]}" config --quiet; then
  fatal "Docker Compose configuration is invalid: ${COMPOSE_FILE}"
fi
pass "Validated the intended local Compose file: ${COMPOSE_FILE}"

required_services=(postgres litellm-proxy honest-analyst-app)
running_services="$("${COMPOSE[@]}" ps --status running --services 2>/dev/null)" ||
  fatal "Could not list services for this Compose project. Is it running from this checkout?"
for service in "${required_services[@]}"; do
  if ! grep -Fxq "${service}" <<<"${running_services}"; then
    fatal "Required Compose service '${service}' is not running. Start the intended stack before running this drill."
  fi
done
pass "Required Compose services are running: ${required_services[*]}."

TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ps5-outage-drill.XXXXXX")"
if ! wait_for_proxy; then
  fatal "LiteLLM proxy is not healthy before the drill; refusing to stop it."
fi
pass "LiteLLM proxy is healthy before the drill."

health_response="${TEMP_DIR}/health.json"
if ! "${PYTHON}" - "${API_BASE_URL}" "${health_response}" <<'PY'
import json
import sys
import urllib.error
import urllib.request

url = sys.argv[1].rstrip("/") + "/api/v1/health"
output_path = sys.argv[2]
try:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:
        data = json.loads(response.read().decode("utf-8"))
except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
    print(f"Backend health request failed: {exc}", file=sys.stderr)
    raise SystemExit(1)

if not isinstance(data, dict):
    print("Backend health response was not a JSON object.", file=sys.stderr)
    raise SystemExit(1)
if data.get("index_loaded") is not True:
    print("Backend health reports index_loaded != true; outage fallback cannot be tested.", file=sys.stderr)
    raise SystemExit(1)
try:
    chunks = int(data.get("total_chunks", 0))
except (TypeError, ValueError):
    chunks = 0
if chunks < 4:
    print(f"Backend reports {chunks} chunks; at least four are required for a Top-4 fallback test.", file=sys.stderr)
    raise SystemExit(1)

with open(output_path, "w", encoding="utf-8") as handle:
    json.dump(data, handle)
print(f"Backend index is loaded with {chunks} chunks.")
PY
then
  fatal "Backend health/index preflight failed. No container was stopped."
fi
pass "Backend API is reachable and has an index with at least four chunks."

eval_output="${TEMP_DIR}/baseline-evaluation.json"
printf '\nStarting a one-question normal evaluation before the outage...\n'
if ! "${PYTHON}" "${PROJECT_ROOT}/eval/run_eval.py" \
  --test-set "${PROJECT_ROOT}/eval/golden_test_set.json" \
  --api-url "${API_BASE_URL}" \
  --limit 1 \
  --timeout "${REQUEST_TIMEOUT_SECONDS}" \
  --token-source none \
  --output "${eval_output}"; then
  fatal "Baseline evaluation could not complete. The proxy has not been stopped."
fi
if ! "${PYTHON}" - "${eval_output}" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        report = json.load(handle)
except (OSError, json.JSONDecodeError) as exc:
    print(f"Could not read baseline evaluation report: {exc}", file=sys.stderr)
    raise SystemExit(1)

results = report.get("individual_results")
if not isinstance(results, list) or len(results) != 1:
    print("Baseline evaluation did not record exactly one question.", file=sys.stderr)
    raise SystemExit(1)
result = results[0]
response = result.get("response")
if result.get("error") or not isinstance(response, dict):
    print(f"Baseline request did not return a valid API response: {result.get('error')}", file=sys.stderr)
    raise SystemExit(1)
status = response.get("system_status")
if response.get("is_fallback_mode") is True or not isinstance(status, dict) or status.get("execution_mode") != "LLM_SYNTHESIZED":
    mode = status.get("execution_mode") if isinstance(status, dict) else None
    print(f"Baseline request did not use normal LLM inference (mode={mode!r}).", file=sys.stderr)
    raise SystemExit(1)
print("Baseline query returned a normal LLM_SYNTHESIZED response.")
PY
then
  fatal "Baseline request failed normal-inference assertions. The proxy has not been stopped."
fi
pass "Baseline evaluation completed while the proxy was online."

project_name="$("${COMPOSE[@]}" config --format json 2>/dev/null | "${PYTHON}" -c 'import json,sys; print(json.load(sys.stdin).get("name", "unknown"))')" ||
  project_name="unknown"
printf '\nSAFETY CONFIRMATION\n'
printf 'This will stop only the local Compose service "litellm-proxy" in project "%s".\n' "${project_name}"
printf 'Docker context: %s (%s)\n' "${docker_context}" "${context_endpoint}"
printf 'Compose file: %s\n' "${COMPOSE_FILE}"
printf 'The script will restart the proxy automatically, including on interruption.\n'
printf 'To continue, type exactly: STOP LOCAL litellm-proxy\n> '
if [[ ! -t 0 ]]; then
  fatal "Interactive confirmation is required; refusing to stop the proxy from non-interactive input."
fi
IFS= read -r confirmation
if [[ "${confirmation}" != "STOP LOCAL litellm-proxy" ]]; then
  fatal "Safety confirmation did not match. No container was stopped."
fi
pass "Operator confirmed the local proxy outage drill."

outage_started="$(date +%s)"
PROXY_MAY_BE_STOPPED=1
if ! "${COMPOSE[@]}" stop litellm-proxy; then
  fatal "Could not stop the local litellm-proxy service."
fi
pass "Stopped the local Compose service 'litellm-proxy'."

printf '\nSending five outage queries (per-request timeout: %ss)...\n' "${REQUEST_TIMEOUT_SECONDS}"
if "${PYTHON}" - "${API_BASE_URL}" "${REQUEST_TIMEOUT_SECONDS}" <<'PY'
import json
import re
import sys
import time
import urllib.error
import urllib.request

base_url = sys.argv[1].rstrip("/")
timeout_seconds = float(sys.argv[2])
questions = [
    "What was total revenue in the latest fiscal year covered by the available filings?",
    "What was operating income in the latest fiscal year covered by the available filings?",
    "What were total assets in the latest fiscal year covered by the available filings?",
    "What was operating cash flow in the latest fiscal year covered by the available filings?",
    "What was research and development expense in the latest fiscal year covered by the available filings?",
]
failures = 0

for index, question in enumerate(questions, 1):
    request = urllib.request.Request(
        base_url + "/api/v1/query",
        data=json.dumps({"question": question}).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    payload = None
    error = None
    status_code = None
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status_code = response.status
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        status_code = exc.code
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except OSError:
            detail = ""
        error = f"HTTP {exc.code}: {detail}"
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        error = f"{type(exc).__name__}: {exc}"
    latency = time.perf_counter() - started

    checks = []
    if error is not None:
        checks.append(f"request failed ({error})")
    elif not isinstance(payload, dict):
        checks.append("response is not a JSON object")
    else:
        if status_code is None or not 200 <= status_code < 300:
            checks.append(f"HTTP status is {status_code}")
        if not isinstance(payload.get("answer"), str):
            checks.append("answer is missing or not a string")
        if payload.get("is_fallback_mode") is not True:
            checks.append("is_fallback_mode is not true")
        system_status = payload.get("system_status")
        if not isinstance(system_status, dict) or system_status.get("execution_mode") != "DEGRADED_PASSAGE_FALLBACK":
            checks.append("execution_mode is not DEGRADED_PASSAGE_FALLBACK")

        citations = payload.get("citations")
        if not isinstance(citations, list) or len(citations) != 4:
            checks.append(f"expected exactly four passage citations, received {len(citations) if isinstance(citations, list) else 'invalid citations'}")
            citations = citations if isinstance(citations, list) else []
        for citation_index, citation in enumerate(citations, 1):
            if not isinstance(citation, dict):
                checks.append(f"citation {citation_index} is not an object")
                continue
            filename = citation.get("pdf_filename")
            page_number = citation.get("pdf_page_number")
            if not isinstance(filename, str) or not filename.strip() or not filename.casefold().endswith(".pdf"):
                checks.append(f"citation {citation_index} has an invalid PDF filename")
            if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
                checks.append(f"citation {citation_index} has an invalid 1-indexed physical page number")

        answer = payload.get("answer", "")
        passage_numbers = re.findall(r"(?m)^\[(\d+)\]\s+", answer) if isinstance(answer, str) else []
        if passage_numbers != ["1", "2", "3", "4"]:
            checks.append(f"expected four numbered passage excerpts [1]-[4], received {passage_numbers}")

    if latency > 5.0:
        checks.append(f"latency {latency:.3f}s exceeded 5.000s")
    if checks:
        failures += 1
        print(f"FAIL: Outage query {index}/5 ({latency:.3f}s): {'; '.join(checks)}", file=sys.stderr)
    else:
        print(f"PASS: Outage query {index}/5 returned four cited passages in {latency:.3f}s.")

raise SystemExit(1 if failures else 0)
PY
then
  pass "All five outage queries met the fallback, Top-4 citation, and 5-second latency assertions."
else
  fail "One or more outage query assertions failed."
  FAILURES=$((FAILURES + 1))
fi

outage_ended="$(date +%s)"
elapsed_outage=$((outage_ended - outage_started))
remaining_outage=$((OUTAGE_SECONDS - elapsed_outage))
if (( remaining_outage > 0 )); then
  printf 'Maintaining the outage for %ss more to complete the configured %ss drill window...\n' \
    "${remaining_outage}" "${OUTAGE_SECONDS}"
  sleep "${remaining_outage}"
fi
pass "Proxy outage window lasted at least ${OUTAGE_SECONDS}s."

if "${COMPOSE[@]}" start litellm-proxy; then
  PROXY_MAY_BE_STOPPED=0
  pass "Restarted the local Compose service 'litellm-proxy'."
else
  fail "Could not restart litellm-proxy."
  FAILURES=$((FAILURES + 1))
fi

if wait_for_proxy; then
  pass "LiteLLM proxy readiness check succeeded after restart."
else
  fail "LiteLLM proxy did not become ready after restart."
  FAILURES=$((FAILURES + 1))
fi

recovery_nonce="$("${PYTHON}" -c 'import secrets; print(secrets.token_hex(4))')"
recovery_question="For recovery check ${recovery_nonce}, what was total revenue in the latest fiscal year covered by the available filings?"
if "${PYTHON}" - "${API_BASE_URL}" "${REQUEST_TIMEOUT_SECONDS}" "${recovery_question}" <<'PY'
import json
import sys
import time
import urllib.error
import urllib.request

url = sys.argv[1].rstrip("/") + "/api/v1/query"
timeout = float(sys.argv[2])
question = sys.argv[3]
request = urllib.request.Request(
    url,
    data=json.dumps({"question": question}).encode("utf-8"),
    headers={"Accept": "application/json", "Content-Type": "application/json"},
    method="POST",
)
started = time.perf_counter()
try:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status = response.status
        payload = json.loads(response.read().decode("utf-8"))
except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
    print(f"Post-restart query failed: {exc}", file=sys.stderr)
    raise SystemExit(1)
latency = time.perf_counter() - started

if not isinstance(payload, dict):
    print("Post-restart query returned a non-object JSON response.", file=sys.stderr)
    raise SystemExit(1)
system_status = payload.get("system_status")
if status < 200 or status >= 300:
    print(f"Post-restart query returned HTTP {status}.", file=sys.stderr)
    raise SystemExit(1)
if payload.get("is_fallback_mode") is True:
    print("Post-restart query is still in fallback mode.", file=sys.stderr)
    raise SystemExit(1)
if not isinstance(system_status, dict) or system_status.get("execution_mode") != "LLM_SYNTHESIZED":
    mode = system_status.get("execution_mode") if isinstance(system_status, dict) else None
    print(f"Expected LLM_SYNTHESIZED after recovery; received {mode!r}.", file=sys.stderr)
    raise SystemExit(1)
tokens_used = payload.get("tokens_used")
if isinstance(tokens_used, bool) or not isinstance(tokens_used, int) or tokens_used < 1:
    print(f"Expected positive LLM token usage after recovery; received {tokens_used!r}.", file=sys.stderr)
    raise SystemExit(1)
if not isinstance(payload.get("answer"), str):
    print("Post-restart query has no string answer.", file=sys.stderr)
    raise SystemExit(1)
print(
    f"Post-restart LLM inference resumed: HTTP {status}, "
    f"execution_mode=LLM_SYNTHESIZED, tokens={tokens_used}, latency={latency:.3f}s."
)
PY
then
  pass "Normal LLM inference resumed after the proxy restart."
else
  fail "Post-restart inference did not satisfy the normal-response assertions."
  FAILURES=$((FAILURES + 1))
fi

if (( FAILURES > 0 )); then
  printf '\nOutage drill finished with %s failed assertion group(s).\n' "${FAILURES}" >&2
  exit 1
fi

printf '\nPASS: Outage fallback drill completed; the local proxy is running and healthy.\n'
