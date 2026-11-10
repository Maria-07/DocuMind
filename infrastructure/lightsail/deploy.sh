#!/usr/bin/env bash
# Deploy (or update) DocuMind on this server with Docker Compose, behind Caddy with automatic HTTPS.
#
#   bash infrastructure/lightsail/deploy.sh <hostname>          # e.g. documind-api.duckdns.org
#   bash infrastructure/lightsail/deploy.sh <hostname> --smoke  # also run the end-to-end smoke test
#   bash infrastructure/lightsail/deploy.sh <hostname> --lean   # fit a 1 GB server (smaller search model, no reranker)
#
# First run: creates .env with fresh random secrets and demo-safe defaults (sign-up closed, cheap models
# only), asks for your LLM API key(s), builds and starts everything, and offers to create the first admin.
# Later runs: rebuild and restart with the existing .env (your keys and secrets are kept).
#
# <hostname> must already point at this server (DNS A record) and ports 80 and 443 must be open.
set -euo pipefail

HOST="${1:-}"
SMOKE="no"
LEAN="no"
for arg in "${@:2}"; do
  case "$arg" in
    --smoke) SMOKE="yes" ;;
    --lean) LEAN="yes" ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done
if [ -z "$HOST" ] || [ "${HOST#-}" != "$HOST" ]; then
  echo "Usage: bash $0 <public hostname, e.g. documind-api.duckdns.org> [--smoke] [--lean]" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
COMPOSE=(docker compose -f infrastructure/docker-compose.yml --env-file .env)

command -v docker >/dev/null || { echo "Docker is not installed. Run bootstrap-host.sh first." >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "Cannot talk to Docker. Log out and back in (docker group), or use sudo." >&2; exit 1; }

# set_env KEY VALUE: set or add KEY in .env. The value travels in the environment, not on the command line.
set_env() {
  DM_KEY="$1" DM_VALUE="$2" python3 - <<'PY'
import os, pathlib, re
key, value = os.environ["DM_KEY"], os.environ["DM_VALUE"]
path = pathlib.Path(".env")
text = path.read_text()
pattern = re.compile(rf"^{re.escape(key)}=.*$", re.M)
line = f"{key}={value}"
if pattern.search(text):
    text = pattern.sub(lambda _m: line, text, count=1)
else:
    text += ("" if text.endswith("\n") else "\n") + line + "\n"
path.write_text(text)
PY
}

has_value() { grep -Eq "^$1=[^[:space:]#]+" .env; }
random_hex() { python3 -c "import secrets; print(secrets.token_hex($1))"; }

if [ ! -f .env ]; then
  echo "==> Creating .env with fresh secrets"
  cp .env.example .env
  chmod 600 .env
  set_env POSTGRES_USER documind
  set_env POSTGRES_DB documind
  set_env POSTGRES_PASSWORD "$(random_hex 24)"   # hex: it goes into a URL
  set_env JWT_SECRET_KEY "$(random_hex 32)"
  set_env QDRANT_API_KEY "$(random_hex 32)"
  # Demo-safe defaults: nobody can create workspaces, and only inexpensive models can be picked.
  set_env REGISTRATION_ENABLED false
  set_env CHAT_MODEL_IDS "gpt-5-nano,gpt-5-mini,gemini-3.5-flash-lite"
  set_env LLM_PROVIDER openai
  set_env LLM_MODEL gpt-5-nano
  set_env LLM_EFFORT low
fi
chmod 600 .env
set_env SITE_ADDRESS "$HOST"

# --lean is remembered in .env, so later updates keep the same settings.
[ "$LEAN" = "yes" ] && set_env DOCUMIND_LEAN true
if grep -Eq "^DOCUMIND_LEAN=true" .env; then
  echo "==> Lean mode: small search model, no reranker, memory caps for a 1 GB server"
  set_env EMBEDDING_MODEL BAAI/bge-small-en-v1.5
  set_env RERANKER_ENABLED false
  set_env WORKER_MAX_JOBS 1
  COMPOSE+=(-f infrastructure/docker-compose.lean.yml)
fi

if ! has_value OPENAI_API_KEY && ! has_value GOOGLE_API_KEY && ! has_value ANTHROPIC_API_KEY; then
  echo "==> LLM API keys (typed input is hidden; press Enter to skip one)"
  read -r -s -p "OpenAI API key: " key; echo; [ -n "$key" ] && set_env OPENAI_API_KEY "$key"
  read -r -s -p "Google (Gemini) API key: " key; echo; [ -n "$key" ] && set_env GOOGLE_API_KEY "$key"
  unset key
  if ! has_value OPENAI_API_KEY && ! has_value GOOGLE_API_KEY; then
    echo "No key entered. Chat will not work until you add one to .env (OPENAI_API_KEY / GOOGLE_API_KEY) and re-run." >&2
  fi
fi

echo "==> Building and starting the stack (the first build takes several minutes)"
"${COMPOSE[@]}" up -d --build

echo "==> Waiting for https://${HOST}/login (needs DNS pointing here and ports 80/443 open)"
up="no"
for _ in $(seq 1 120); do
  if curl -fsS -o /dev/null --max-time 10 "https://${HOST}/login"; then up="yes"; break; fi
  sleep 5
done
if [ "$up" != "yes" ]; then
  echo "The site did not come up in 10 minutes. State and recent logs:" >&2
  "${COMPOSE[@]}" ps >&2 || true
  "${COMPOSE[@]}" logs --tail 40 caddy backend >&2 || true
  echo "Most common causes: the DNS name does not point at this server yet, or ports 80/443 are closed in the" >&2
  echo "Lightsail firewall (Networking tab). See docs/deploy-vercel-lightsail.md, Troubleshooting." >&2
  exit 1
fi
echo "    up: https://${HOST}"

admin_email=""
admin_password=""
if [ -t 0 ]; then
  read -r -p "Create an admin account now? [Y/n] " answer
  if [ "${answer:-Y}" != "n" ] && [ "${answer:-Y}" != "N" ]; then
    read -r -p "  Admin e-mail: " admin_email
    read -r -p "  Admin full name: " admin_name
    read -r -p "  Organization name [Demo]: " admin_org
    read -r -s -p "  Password (10+ characters, a letter and a digit): " admin_password; echo
    if DOCUMIND_ADMIN_PASSWORD="$admin_password" "${COMPOSE[@]}" exec -T -e DOCUMIND_ADMIN_PASSWORD backend \
        python -m app.cli create-admin --email "$admin_email" --name "$admin_name" --organization "${admin_org:-Demo}"; then
      :
    else
      echo "  (Not created. You can retry with: ${COMPOSE[*]} exec backend python -m app.cli create-admin ...)" >&2
      admin_password=""
    fi
  fi
fi

if [ "$SMOKE" = "yes" ]; then
  if [ -z "$admin_email" ] || [ -z "$admin_password" ]; then
    echo "Skipping the smoke test: it needs the admin account created above." >&2
  else
    echo "==> Smoke test (first run downloads the AI models: allow several minutes)"
    python3 -m venv /tmp/documind-smoke
    /tmp/documind-smoke/bin/pip install --quiet python-docx
    /tmp/documind-smoke/bin/python infrastructure/smoke_test.py "https://${HOST}" --production \
      --email "$admin_email" --password "$admin_password"
  fi
fi

echo
echo "DocuMind is running at https://${HOST}"
echo "Next: put the Vercel frontend in front of it (docs/deploy-vercel-lightsail.md, section 6)."
