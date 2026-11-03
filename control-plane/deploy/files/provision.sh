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

# Sandbox reaper.
install -d -m 0755 /opt/arz
install -m 0755 arz-reaper.py /opt/arz/arz-reaper.py
install -m 0644 arz-reaper.service arz-reaper.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now arz-reaper.timer >/dev/null

echo "provision: ok"
