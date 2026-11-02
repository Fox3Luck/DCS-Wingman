#!/bin/bash
# DCS Wingman host provisioning (Debian 13). Idempotent: safe to rerun. Run on the host:
#   sudo WINGMAN_ADMIN_CIDR=<cidr allowed to SSH> bash vm-provision.sh
#
# Optional environment:
#   WINGMAN_REPO            git URL to deploy (default https://github.com/Fox3Luck/DCS-Wingman.git)
#   WINGMAN_REF             branch or tag (default main)
#   WINGMAN_HOSTNAME        hostname to set (default dcs-wingman)
#   WINGMAN_TIMEZONE        timezone (default UTC)
#   WINGMAN_TS_LOGIN_SERVER Tailscale control server URL (set it to join a private mesh)
#   WINGMAN_TS_AUTHKEY      Tailscale pre-auth key for that server (single use)
#   WINGMAN_SSH_IFACE       interface that may reach SSH, e.g. tailscale0 (optional, in
#                           addition to WINGMAN_ADMIN_CIDR)
#
# Result: nginx (loopback only) serving the site and proxying /v1 to the service,
# fail2ban (sshd + nginx jails), unattended-upgrades, UFW (SSH from the admin network
# only, nothing public), optional Tailscale join, cloudflared installed from Cloudflare's
# signed apt repo (tunnel config is written separately by cloudflare-tunnel.sh), service
# user + venv + unit. All public traffic is expected to arrive through the tunnel.
#
# Lockout guard: sshd is hardened (no root, no passwords) and UFW is enabled only after
# this script has verified that (a) a non-root account with an authorized key and sudo
# exists and (b) if a Tailscale control server was requested, the host holds a mesh
# IPv4. Otherwise it aborts before touching sshd or the firewall. Recovery if both are
# wrong anyway: the hypervisor console.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

REPO="${WINGMAN_REPO:-https://github.com/Fox3Luck/DCS-Wingman.git}"
REF="${WINGMAN_REF:-main}"
HOST_NAME="${WINGMAN_HOSTNAME:-dcs-wingman}"
TZ_NAME="${WINGMAN_TIMEZONE:-UTC}"
ADMIN_CIDR="${WINGMAN_ADMIN_CIDR:-}"
SSH_IFACE="${WINGMAN_SSH_IFACE:-}"
APP=/opt/dcs-wingman
DATA=/var/lib/dcs-wingman
ENVF=/etc/dcs-wingman/env
APT="apt-get -o DPkg::Lock::Timeout=600"

if [ -z "${ADMIN_CIDR}" ] && [ -z "${SSH_IFACE}" ]; then
  echo "ABORT: set WINGMAN_ADMIN_CIDR (and/or WINGMAN_SSH_IFACE) so SSH stays reachable after UFW is enabled." >&2
  exit 2
fi

echo "== packages"
${APT} update -q
${APT} install -y -q qemu-guest-agent nginx fail2ban unattended-upgrades ufw rsyslog \
  python3-venv python3-pip git curl gnupg ca-certificates
systemctl enable --now qemu-guest-agent rsyslog

echo "== identity and time"
hostnamectl set-hostname "${HOST_NAME}"
grep -q "${HOST_NAME}" /etc/hosts || echo "127.0.1.1 ${HOST_NAME}" >> /etc/hosts
timedatectl set-timezone "${TZ_NAME}"

echo "== unattended upgrades"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

MESH_IP=""
if [ -n "${WINGMAN_TS_LOGIN_SERVER:-}" ]; then
  echo "== tailscale"
  if ! command -v tailscale >/dev/null; then
    curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.noarmor.gpg -o /usr/share/keyrings/tailscale-archive-keyring.gpg
    curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.tailscale-keyring.list -o /etc/apt/sources.list.d/tailscale.list
    ${APT} update -q && ${APT} install -y -q tailscale
  fi
  systemctl enable --now tailscaled
  if ! tailscale ip -4 >/dev/null 2>&1 && [ -n "${WINGMAN_TS_AUTHKEY:-}" ]; then
    tailscale up --login-server "${WINGMAN_TS_LOGIN_SERVER}" --authkey "${WINGMAN_TS_AUTHKEY}" \
      --hostname "${HOST_NAME}" --accept-routes --accept-dns=false
  fi
  MESH_IP="$(tailscale ip -4 2>/dev/null || true)"
  if [ -z "${MESH_IP}" ]; then
    echo "ABORT: tailscale has no mesh IPv4. Pass WINGMAN_TS_AUTHKEY or join by hand, then rerun." >&2
    exit 3
  fi
  echo "mesh ip: ${MESH_IP}"
fi

echo "== lockout guard (a non-root sudo account with an authorized key)"
ADMIN_OK=0
for home in /home/*; do
  u="$(basename "${home}")"
  [ -s "${home}/.ssh/authorized_keys" ] || continue
  if id -nG "${u}" 2>/dev/null | grep -qw sudo || sudo -l -U "${u}" 2>/dev/null | grep -q "NOPASSWD"; then
    ADMIN_OK=1
    echo "admin account: ${u} (authorized key + sudo)"
  fi
done
if [ "${ADMIN_OK}" != "1" ]; then
  echo "ABORT: no non-root account with an authorized key and sudo. sshd hardening would lock this host." >&2
  exit 3
fi

echo "== sshd hardening (keys only, no root)"
cat > /etc/ssh/sshd_config.d/00-wingman-hardening.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
X11Forwarding no
MaxAuthTries 3
EOF
sshd -t
systemctl reload ssh

echo "== cloudflared (binary from Cloudflare's signed apt repo; tunnel config comes from cloudflare-tunnel.sh)"
if [ ! -f /usr/share/keyrings/cloudflare-main.gpg ]; then
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
fi
if [ ! -f /etc/apt/sources.list.d/cloudflared.list ]; then
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" \
    > /etc/apt/sources.list.d/cloudflared.list
  ${APT} update -q
fi
${APT} install -y -q cloudflared
mkdir -p /etc/cloudflared

echo "== firewall: deny inbound except SSH from the admin network; all web traffic arrives via the tunnel"
# No reset: every line below is idempotent (ufw skips rules that already exist).
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
[ -n "${SSH_IFACE}" ]  && ufw allow in on "${SSH_IFACE}" to any port 22 proto tcp >/dev/null
[ -n "${ADMIN_CIDR}" ] && ufw allow in from "${ADMIN_CIDR}" to any port 22 proto tcp >/dev/null
ufw --force enable >/dev/null

echo "== service user, code, venv"
id wingman >/dev/null 2>&1 || useradd --system --home ${APP} --shell /usr/sbin/nologin wingman
mkdir -p ${DATA} /etc/dcs-wingman
if [ -d ${APP}/.git ]; then
  git -C ${APP} fetch -q origin && git -C ${APP} checkout -q "${REF}" && git -C ${APP} pull -q --ff-only
else
  git clone -q --branch "${REF}" "${REPO}" ${APP}
fi
python3 -m venv ${APP}/service/.venv
${APP}/service/.venv/bin/pip install -q --upgrade pip
${APP}/service/.venv/bin/pip install -q -r ${APP}/service/requirements.txt
chown -R wingman:wingman ${DATA}
chown -R root:root ${APP}

echo "== secrets (generated once, never printed)"
if [ ! -f ${ENVF} ]; then
  umask 077
  cat > ${ENVF} <<EOF
WINGMAN_DB=${DATA}/wingman.db
WINGMAN_PEPPER=$(openssl rand -hex 32)
WINGMAN_ADMIN_KEY=$(openssl rand -hex 32)
WINGMAN_PRESENCE_TTL=300
WINGMAN_GREET_GAP=1800
WINGMAN_TRUST_PROXY=1
WINGMAN_DOCS=0
EOF
  umask 022
fi
# systemd reads EnvironmentFile as root before dropping to the service user, so the
# service user never needs to read it from disk.
chown root:root ${ENVF} && chmod 0600 ${ENVF}

echo "== site (wrap the artifact-style page in a document skeleton)"
# site/index.html is a head+body fragment (title, styles, then markup). The HTML parser
# closes <head> and opens <body> at the first flow element, which is the same thing the
# artifact host does at publish time, so the fragment is wrapped unchanged.
mkdir -p ${APP}/site-dist
{
  printf '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
  cat ${APP}/site/index.html
  printf '\n</html>\n'
} > ${APP}/site-dist/index.html

echo "== systemd unit"
install -m 0644 ${APP}/service/deploy/dcs-wingman.service /etc/systemd/system/dcs-wingman.service
systemctl daemon-reload
systemctl enable --now dcs-wingman
systemctl restart dcs-wingman

echo "== fail2ban (filter first, so a missing file fails before anything is enabled)"
install -m 0644 ${APP}/service/deploy/fail2ban/nginx-traversal.conf /etc/fail2ban/filter.d/nginx-traversal.conf
install -m 0644 ${APP}/service/deploy/fail2ban/jail.local /etc/fail2ban/jail.local
mkdir -p /etc/fail2ban/jail.d

echo "== nginx"
install -m 0644 ${APP}/service/deploy/nginx-dcs-wingman.conf /etc/nginx/sites-available/dcs-wingman
ln -sf /etc/nginx/sites-available/dcs-wingman /etc/nginx/sites-enabled/dcs-wingman
rm -f /etc/nginx/sites-enabled/default
# restart, not reload: the listen address moves from 0.0.0.0:80 (Debian default site)
# to 127.0.0.1:80 and a reload cannot rebind.
nginx -t && systemctl restart nginx

systemctl enable --now fail2ban
systemctl restart fail2ban

echo "== verify"
sleep 2
curl -fsS http://127.0.0.1/healthz && echo
curl -fsS -o /dev/null -w "site http %{http_code}\n" http://127.0.0.1/
fail2ban-client status | sed -n '1,6p'
ufw status | sed -n '1,8p'
[ -n "${MESH_IP}" ] && echo "mesh ip ${MESH_IP}"
echo "provisioning complete"
