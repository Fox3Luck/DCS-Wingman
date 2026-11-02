#!/bin/bash
# Create (or reuse) the DCS Wingman Cloudflare tunnel and its DNS records purely via
# the Cloudflare API, and emit the files the host needs. Run it wherever the Cloudflare
# credentials live; the credentials never leave that machine.
#
#   CF_EMAIL=... CF_API_KEY=... CF_ACCOUNT=... CF_ZONE=... \
#   bash cloudflare-tunnel.sh <tunnel-name> <public-hostname> [<second-hostname>] > wingman-cloudflared.tar
#
# Idempotence:
#   - A tunnel with that name already exists: the script stops (exit 3) unless
#     WINGMAN_REUSE_TUNNEL=1, in which case it emits config.yml only (Cloudflare never
#     returns an existing tunnel secret; the VM keeps its credentials file).
#   - A CNAME already exists: left alone if it points at this tunnel, updated if not.
#     Only CNAME records are considered; an A or TXT record of the same name is never
#     touched (the API call then fails and the record must be sorted out by hand).
#   - WINGMAN_SKIP_DNS=1 skips the DNS step entirely (tunnel + bundle only).
#
# Output on stdout is a tar of /etc/cloudflared content; it contains the tunnel secret
# on first creation, so never tee or log that stream. On the VM:
#   sudo tar -C /etc/cloudflared -xf wingman-cloudflared.tar && sudo cloudflared service install \
#     && sudo systemctl enable --now cloudflared
set -euo pipefail
NAME="$1"; HOST1="$2"; HOST2="${3:-}"
for v in CF_EMAIL CF_API_KEY CF_ACCOUNT CF_ZONE; do [ -n "${!v:-}" ] || { echo "missing $v" >&2; exit 2; }; done
API=https://api.cloudflare.com/client/v4
auth=(-H "X-Auth-Email: ${CF_EMAIL}" -H "X-Auth-Key: ${CF_API_KEY}" -H "Content-Type: application/json")

# JSON in and out goes through python; nothing is built by shell interpolation or eval.
# The tunnel secret travels in the environment (TUNNEL_SECRET), never on a command line.
tunnel_body() { TUNNEL_SECRET="$2" python3 -c 'import json,os,sys; print(json.dumps({"name": sys.argv[1], "tunnel_secret": os.environ["TUNNEL_SECRET"], "config_src": "local"}))' "$1"; }
cname_body()  { python3 -c 'import json,sys; print(json.dumps({"type": "CNAME", "name": sys.argv[1], "content": sys.argv[2], "proxied": True, "comment": "DCS Wingman tunnel"}))' "$1" "$2"; }
creds_body()  { TUNNEL_SECRET="$4" python3 -c 'import json,os,sys; print(json.dumps({"AccountTag": sys.argv[1], "TunnelID": sys.argv[2], "TunnelName": sys.argv[3], "TunnelSecret": os.environ["TUNNEL_SECRET"]}))' "$1" "$2" "$3"; }
# first_id: id of the first result or empty. record: "<id> <content>" of the first result or "- -".
# created: "<name> -> <content>" of a single-object result.
jget() {
  python3 -c '
import json, sys
mode = sys.argv[1]
d = json.load(sys.stdin)
if not d.get("success"):
    sys.stderr.write("cloudflare api error: %s\n" % d.get("errors")); sys.exit(4)
r = d.get("result")
if mode == "first_id":
    print(r[0]["id"] if r else "")
elif mode == "record":
    print("%s %s %s" % (r[0]["id"], r[0]["content"], str(r[0].get("proxied")).lower()) if r else "- - -")
elif mode == "id":
    print(r["id"])
elif mode == "created":
    print("%s -> %s" % (r["name"], r["content"]))
' "$1"
}

existing=$(curl -fsS -G "${API}/accounts/${CF_ACCOUNT}/cfd_tunnel" "${auth[@]}" --data-urlencode "name=${NAME}" --data-urlencode "is_deleted=false" | jget first_id)
SECRET=""
if [ -n "${existing}" ]; then
  if [ "${WINGMAN_REUSE_TUNNEL:-0}" != "1" ]; then
    echo "tunnel ${NAME} already exists (${existing}). Set WINGMAN_REUSE_TUNNEL=1 to reuse it (config only), or delete it first." >&2
    exit 3
  fi
  TID="${existing}"
  echo "tunnel ${NAME} = ${TID} (reused, credentials not re-emitted)" >&2
else
  SECRET=$(openssl rand -base64 32)
  TID=$(curl -fsS -X POST "${API}/accounts/${CF_ACCOUNT}/cfd_tunnel" "${auth[@]}" --data "$(tunnel_body "${NAME}" "${SECRET}")" | jget id)
  echo "tunnel ${NAME} = ${TID} (created)" >&2
fi
TARGET="${TID}.cfargotunnel.com"

dns() {
  local host="$1" rid rcontent rproxied
  read -r rid rcontent rproxied < <(curl -fsS -G "${API}/zones/${CF_ZONE}/dns_records" "${auth[@]}" \
    --data-urlencode "name=${host}" --data-urlencode "type=CNAME" | jget record)
  if [ "${rid}" = "-" ]; then
    echo "dns created $(curl -fsS -X POST "${API}/zones/${CF_ZONE}/dns_records" "${auth[@]}" --data "$(cname_body "${host}" "${TARGET}")" | jget created)" >&2
  elif [ "${rcontent}" = "${TARGET}" ] && [ "${rproxied}" = "true" ]; then
    echo "dns ok ${host} -> ${TARGET} (unchanged, proxied)" >&2
  else
    echo "dns updated $(curl -fsS -X PATCH "${API}/zones/${CF_ZONE}/dns_records/${rid}" "${auth[@]}" --data "$(cname_body "${host}" "${TARGET}")" | jget created)" >&2
  fi
}
if [ "${WINGMAN_SKIP_DNS:-0}" = "1" ]; then
  echo "DNS step skipped: CNAME ${HOST1}${HOST2:+ and ${HOST2}} -> ${TARGET} (proxied)" >&2
else
  dns "${HOST1}"
  [ -n "${HOST2}" ] && dns "${HOST2}"
fi

work=$(mktemp -d)
umask 077
if [ -n "${SECRET}" ]; then
  creds_body "${CF_ACCOUNT}" "${TID}" "${NAME}" "${SECRET}" > "${work}/${TID}.json"
fi
{
  echo "tunnel: ${TID}"
  echo "credentials-file: /etc/cloudflared/${TID}.json"
  echo "no-autoupdate: true"
  echo "ingress:"
  echo "  - hostname: ${HOST1}"
  echo "    service: http://127.0.0.1:80"
  [ -n "${HOST2}" ] && { echo "  - hostname: ${HOST2}"; echo "    service: http://127.0.0.1:80"; }
  echo "  - service: http_status:404"
} > "${work}/config.yml"
tar -C "${work}" -cf - .
rm -rf "${work}"
