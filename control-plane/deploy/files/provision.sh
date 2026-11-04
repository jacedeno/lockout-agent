#!/usr/bin/env bash
# Provision the control plane VM. Idempotent; run as root from the unpacked deploy directory
# (control-plane/deploy/deploy.py ships it). Templates are already rendered by deploy.py.
set -euo pipefail
cd "$(dirname "$0")"
export DEBIAN_FRONTEND=noninteractive

cloud-init status --wait >/dev/null 2>&1 || true

# 1 GiB swap: headroom for the 2 GB plan.
if [ ! -f /swapfile ]; then
	fallocate -l 1G /swapfile
	chmod 600 /swapfile
	mkswap /swapfile >/dev/null
	swapon /swapfile
	echo '/swapfile none swap sw 0 0' >>/etc/fstab
fi

# SSH: keys only.
install -m 0644 sshd-00-arz.conf /etc/ssh/sshd_config.d/00-arz.conf
sshd -t
systemctl reload ssh

# Packages: nftables, Caddy from its official apt repository.
if ! command -v caddy >/dev/null || ! command -v nft >/dev/null; then
	apt-get update -q
	apt-get install -yq debian-keyring debian-archive-keyring apt-transport-https curl gnupg nftables python3-venv
	curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key |
		gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
	curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt >/etc/apt/sources.list.d/caddy-stable.list
	apt-get update -q
	apt-get install -yq caddy
fi

# Host firewall: nftables replaces ufw.
if command -v ufw >/dev/null; then
	ufw --force disable >/dev/null || true
	systemctl disable --now ufw >/dev/null 2>&1 || true
fi
nft -c -f nftables.conf
install -m 0644 nftables.conf /etc/nftables.conf
systemctl enable nftables >/dev/null
systemctl restart nftables

# Caddy (TLS via Let's Encrypt; DNS is DNS-only on Cloudflare).
install -m 0644 Caddyfile /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
systemctl enable caddy >/dev/null
systemctl reload-or-restart caddy

# Control plane package (jobs, sandbox lifecycle) and job storage.
install -d -m 0755 /opt/arz
rm -rf /opt/arz/app.new
cp -r app /opt/arz/app.new
rm -rf /opt/arz/app
mv /opt/arz/app.new /opt/arz/app
install -d -m 0700 /var/lib/arz /var/lib/arz/jobs

# Job runtime: a venv with the receipts library (Ed25519 via cryptography), and vibkit with the
# same numpy/scipy pins as the sandbox image, for the control plane's own reference diagnosis.
# Jobs run with /opt/arz/venv/bin/python -m arz.<job>.
[ -x /opt/arz/venv/bin/python ] || python3 -m venv /opt/arz/venv
/opt/arz/venv/bin/pip install -q --disable-pip-version-check numpy==2.1.3 scipy==1.14.1 ./pkg/receipts
/opt/arz/venv/bin/pip install -q --disable-pip-version-check --no-deps --force-reinstall ./pkg/vibkit
/opt/arz/venv/bin/pip install -q --disable-pip-version-check fastapi==0.115.12 uvicorn==0.34.2 python-multipart==0.0.20

# Synthetic datasets, regenerated deterministically and checked against the repo's hashes.
rm -rf /opt/arz/datasets/synth.new
# --trend adds trends/<name>.json, the synthetic weekly history (derived, not in SHA256SUMS).
/opt/arz/venv/bin/python -I synth/generate.py --out /opt/arz/datasets/synth.new --trend >/dev/null
if ! diff <(sort /opt/arz/datasets/synth.new/SHA256SUMS) <(sort synth/SHA256SUMS.expected) >/dev/null; then
	echo "synthetic datasets do not match SHA256SUMS.expected" >&2
	diff <(sort /opt/arz/datasets/synth.new/SHA256SUMS) <(sort synth/SHA256SUMS.expected) >&2 || true
	exit 1
fi
rm -rf /opt/arz/datasets/synth
mv /opt/arz/datasets/synth.new /opt/arz/datasets/synth

# The web app runs as its own user; it reads the signing key through its group and owns the jobs.
id arz >/dev/null 2>&1 || useradd --system --home-dir /var/lib/arz --shell /usr/sbin/nologin arz
chown root:arz /etc/arz /etc/arz/receipt-signing.pem
chmod 0750 /etc/arz
chmod 0640 /etc/arz/receipt-signing.pem
chown -R arz:arz /var/lib/arz
install -m 0644 arz-web.service /etc/systemd/system/arz-web.service

# Sandbox reaper.
install -d -m 0755 /opt/arz
install -m 0755 arz-reaper.py /opt/arz/arz-reaper.py
install -m 0644 arz-reaper.service arz-reaper.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now arz-reaper.timer >/dev/null
systemctl enable arz-web.service >/dev/null
systemctl restart arz-web.service
for _ in $(seq 1 30); do curl -sf http://127.0.0.1:8000/api/status >/dev/null && break; sleep 1; done
curl -sf http://127.0.0.1:8000/api/status >/dev/null || { journalctl -u arz-web -n 30 --no-pager >&2; exit 1; }

echo "provision: ok"
