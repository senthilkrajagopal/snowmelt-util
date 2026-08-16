#!/usr/bin/env bash
# KAGENT on Contabo: agentic AI (controller + UI + MCP tools + built-in SRE
# agents) in its own `kagent` namespace, model served by Mistral.
# Trigger: "deploy kagent to contabo server".
#
# Two releases, CRDs first — the kagent chart ships Agent/ModelConfig custom
# resources, and `helm install` resolves every manifest's kind against the API
# server BEFORE creating anything, so the CRDs cannot ride along in the same
# release. See charts/snowmelt-ai/snowmelt-kagent-crds/Chart.yaml (snowmelt repo).
#
# THE API KEY IS NOT IN GIT. Create it once, before the first run:
#   kubectl create secret generic kagent-mistral -n kagent \
#     --from-literal=OPENAI_API_KEY=<your mistral key>
# (the key is named OPENAI_API_KEY because Mistral is driven through kagent's
# OpenAI-compatible provider — see charts/snowmelt-ai/snowmelt-kagent/values.yaml).
set -uo pipefail
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
UTIL=/root/snowmelt-util
# The AI charts live in the SNOWMELT repo under charts/snowmelt-ai/, not here.
# They moved because they are runtime components of the product (the copilot
# talks to them) rather than ops assets, which is the line this repo draws.
# Override when the checkout is somewhere else.
SM=${SNOWMELT_SRC:-/root/sm-agw}
AI="$SM/charts/snowmelt-ai"
CRDS="$AI/snowmelt-kagent-crds"
KA="$AI/snowmelt-kagent"
DEP="$UTIL/deploy"
NS=kagent
echo "===== KAGENT DEPLOY $(date -u) ====="

echo "--- namespace ---"
kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f - 2>&1 | tail -1

echo "--- preflight: model API key secret ---"
if ! kubectl get secret kagent-mistral -n "$NS" >/dev/null 2>&1; then
  echo "MISSING secret kagent-mistral in ns $NS — the ModelConfig would point at"
  echo "nothing and every agent would fail on its first turn. Create it with:"
  echo "  kubectl create secret generic kagent-mistral -n $NS \\"
  echo "    --from-literal=OPENAI_API_KEY=<your mistral key>"
  exit 10
fi

# The gateway cutover has its own credential, and getting it wrong does not fail
# at deploy time — it fails on every agent's first turn, as a 401 from the front
# door that reads like the model is broken. Checked here only when the overlay
# actually asks for it, so the direct-to-Mistral path stays a one-Secret deploy.
if grep -qE '^\s*enabled:\s*true' <(sed -n '/^llmViaGateway:/,/^[a-zA-Z]/p' "$DEP/contabo-kagent.yaml" 2>/dev/null); then
  echo "--- preflight: agentgateway front-door key (llmViaGateway is on) ---"
  if ! kubectl get secret kagent-agentgateway -n "$NS" >/dev/null 2>&1; then
    echo "MISSING secret kagent-agentgateway in ns $NS — every agent would reach"
    echo "the gateway and be refused at the front door. Its value is the"
    echo "agentgateway chart's apiKey.value, NOT the Mistral key:"
    echo "  kubectl create secret generic kagent-agentgateway -n $NS \\"
    echo "    --from-literal=OPENAI_API_KEY=<agentgateway apiKey.value>"
    exit 11
  fi
  # The gateway holds the real upstream credential. Its absence is not this
  # script's to fix (different namespace, different release) but it is this
  # script's to WARN about, because the symptom lands on kagent.
  if ! kubectl get secret agentgateway-model-key -n agentgateway >/dev/null 2>&1; then
    echo "WARNING: ns agentgateway has no Secret agentgateway-model-key, so the"
    echo "gateway has no upstream Mistral credential — agents will authenticate"
    echo "at the front door and then fail against api.mistral.ai."
  fi
fi
echo "ok: kagent-mistral present"

echo "--- preflight: oauth2-proxy secret ---"
# client-id / client-secret / cookie-secret, exactly those key names — the
# oauth2-proxy subchart's deployment reads them by key from this Secret.
#
# cookie-secret MUST BE 16, 24 OR 32 CHARACTERS — oauth2-proxy measures the
# literal value to pick an AES key size, and exits at startup if it is any
# other length. `openssl rand -base64 32` is the obvious thing to reach for and
# is WRONG: it emits 44 characters, and oauth2-proxy's fallback base64 decode
# is RawURLEncoding, which rejects the padding and +/ that standard base64
# produces. `openssl rand -hex 16` gives exactly 32 characters and always works.
if ! kubectl get secret kagent-oauth2-proxy -n "$NS" >/dev/null 2>&1; then
  echo "MISSING secret kagent-oauth2-proxy in ns $NS — oauth2-proxy would"
  echo "CrashLoopBackOff and the UI would be unreachable. Create it with:"
  echo "  kubectl create secret generic kagent-oauth2-proxy -n $NS \\"
  echo "    --from-literal=client-id=kagent \\"
  echo "    --from-literal=client-secret=<realm client secret> \\"
  echo "    --from-literal=cookie-secret=\$(openssl rand -hex 16)"
  exit 11
fi
CS=$(kubectl get secret kagent-oauth2-proxy -n "$NS" -o jsonpath='{.data.cookie-secret}' | base64 -d | wc -c)
case "$CS" in
  16|24|32) echo "ok: kagent-oauth2-proxy present (cookie-secret $CS chars)" ;;
  *) echo "BAD cookie-secret length: $CS chars, must be 16/24/32."
     echo "Fix with: kubectl create secret generic kagent-oauth2-proxy -n $NS \\"
     echo "  --from-literal=client-id=kagent \\"
     echo "  --from-literal=client-secret=<realm client secret> \\"
     echo "  --from-literal=cookie-secret=\$(openssl rand -hex 16) \\"
     echo "  --dry-run=client -o yaml | kubectl apply -f -"
     exit 13 ;;
esac

echo "--- preflight: Keycloak discovery reachable + issuer matches ---"
# oauth2-proxy resolves the issuer at STARTUP and exits if discovery fails, so
# a Keycloak that is down or advertising a different issuer is worth catching
# here rather than in a CrashLoopBackOff.
ISS=$(grep -A1 "OIDC_ISSUER_URL" "$DEP/contabo-kagent.yaml" | grep value: | sed 's/.*value: *"\(.*\)"/\1/')
ADV=$(curl -s --max-time 10 "$ISS/.well-known/openid-configuration" \
  | sed -n 's/.*"issuer":"\([^"]*\)".*/\1/p')
if [ "$ADV" != "$ISS" ]; then
  echo "ISSUER MISMATCH — oauth2-proxy will refuse to start."
  echo "  configured: $ISS"
  echo "  advertised: ${ADV:-<discovery unreachable>}"
  echo "Check KC_HOSTNAME in deploy/contabo-extras-keycloak.yaml (it must"
  echo "include the /auth path) and that the keycloak pod is up."
  exit 12
fi
echo "ok: issuer $ISS"

echo "--- CRDs (must precede the main release) ---"
helm upgrade --install kagent-crds "$CRDS" \
  -n "$NS" --wait --timeout 3m 2>&1 | grep -iE "STATUS|REVISION|Error"

echo "--- kagent ---"
helm upgrade --install kagent "$KA" -f "$DEP/contabo-kagent.yaml" \
  -n "$NS" --wait=false 2>&1 | grep -iE "STATUS|REVISION|Error"

echo "===== APPLIED $(date -u); polling readiness ====="
for i in $(seq 1 20); do
  nr=$(kubectl get pods -n "$NS" --no-headers 2>/dev/null | grep -vcE "Running|Completed")
  echo "[poll $i] not-ready=$nr"
  [ "$nr" = "0" ] && { echo "ALL READY"; break; }
  sleep 15
done

echo "--- pods ---"
kubectl get pods -n "$NS"
echo "--- agents (CRs reconciled by the controller) ---"
kubectl get agents -n "$NS" 2>&1 | head -20
echo "--- modelconfig ---"
kubectl get modelconfig -n "$NS" -o custom-columns=NAME:.metadata.name,PROVIDER:.spec.provider,MODEL:.spec.model 2>&1 | head -5
echo "--- oauth2-proxy ---"
kubectl get pods -n "$NS" -l app.kubernetes.io/name=oauth2-proxy --no-headers 2>&1 | head -3
echo "--- UI ---"
echo "http://62.171.163.164:30082  (behind oauth2-proxy → Keycloak realm 'snowmelt')"
# Deliberately NOT naming users here. The realm's users come from the chart's
# realmImport ON FIRST BOOT ONLY, and this Keycloak has been running since
# 2026-08-09 with hand-made accounts, so git and the live realm have diverged.
# Ask the realm rather than trusting a hardcoded hint:
echo "  realm users:"
kubectl exec -n default snowmelt-extras-keycloak-0 -- sh -c '
  /opt/keycloak/bin/kcadm.sh config credentials --server http://localhost:8080/auth \
    --realm master --user admin --password "${KC_ADMIN_PASSWORD:-sn0wmelt}" >/dev/null 2>&1
  /opt/keycloak/bin/kcadm.sh get users -r snowmelt --fields username 2>/dev/null' \
  | grep -o '"username" : "[^"]*"' | sed 's/^/    /' || echo "    (could not query keycloak)"
echo "===== END $(date -u) ====="
