#!/usr/bin/env bash
# Turn a fresh Ubuntu 24.04 Vultr instance into the sandbox snapshot. Run as root from a copy
# of the repo's sandbox/ and redteam/ directories. The last step removes the build SSH key and
# resets cloud-init, so take the snapshot right after this exits and never log in again.
set -euo pipefail

VPC_SUBNET="${VPC_SUBNET:-10.50.0.0/24}"
# The only address allowed to SSH in. Baked into the image: re-bake if the control plane's VPC
# address changes (vultr-arz-control-plane.env, VULTR_VPC_IP).
CONTROL_PLANE_IP="${CONTROL_PLANE_IP:?set CONTROL_PLANE_IP to the control plane VPC address}"
SRC="$(cd "$(dirname "$0")/../.." && pwd)"
export DEBIAN_FRONTEND=noninteractive

echo "== packages"
apt-get update -q
apt-get install -y -q --no-install-recommends docker.io nftables curl ca-certificates gnupg

echo "== gVisor (optional second container boundary)"
curl -fsSL https://gvisor.dev/archive.key | gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" \
  > /etc/apt/sources.list.d/gvisor.list
apt-get update -q
apt-get install -y -q runsc

echo "== runner image"
docker build -q -t arz-runner:latest -f "$SRC/sandbox/image/runner/Dockerfile" "$SRC"

echo "== docker: no bridge, no iptables; containers only ever get --network none"
cat > /etc/docker/daemon.json <<JSON
{
  "bridge": "none",
  "iptables": false,
  "ip6tables": false,
  "runtimes": { "runsc": { "path": "/usr/bin/runsc" } }
}
JSON
systemctl restart docker
docker run --rm --network none arz-runner:latest -c "import vibkit; print('runner ok, vibkit', vibkit.__version__)"
docker run --rm --network none --runtime runsc arz-runner:latest -c "print('runsc ok')"

echo "== tooling"
install -d /opt/arz /etc/arz
cp -r "$SRC/sandbox/runner" "$SRC/redteam" /opt/arz/
chmod +x /opt/arz/runner/arz-run /opt/arz/redteam/run.py

echo "== firewall (enabled for next boot, not applied now)"
sed -e "s#@CONTROL_PLANE_IP@#$CONTROL_PLANE_IP#g" \
    -e 's#@METADATA_RULE@#ip daddr 169.254.169.254 tcp dport 80 accept comment "boot phase only"#' \
    "$SRC/sandbox/image/arz-firewall.nft.in" > /etc/nftables.conf
sed -e "s#@CONTROL_PLANE_IP@#$CONTROL_PLANE_IP#g" -e 's#@METADATA_RULE@##' \
    "$SRC/sandbox/image/arz-firewall.nft.in" > /etc/arz/firewall-strict.nft
nft -c -f /etc/nftables.conf
nft -c -f /etc/arz/firewall-strict.nft
grep -q "@" /etc/nftables.conf /etc/arz/firewall-strict.nft && { echo "unrendered firewall template"; exit 1; }
install -m 0644 "$SRC/sandbox/image/arz-lockdown.service" /etc/systemd/system/arz-lockdown.service
ufw disable || true
systemctl disable ufw.service || true
systemctl enable nftables.service arz-lockdown.service

echo "== ssh: keys only"
install -m 0644 "$SRC/sandbox/image/sshd-00-arz.conf" /etc/ssh/sshd_config.d/00-arz.conf
sshd -t

echo "== quiet services that would only time out without egress"
systemctl disable apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service \
  motd-news.timer snapd.service snapd.socket snapd.seeded.service 2>/dev/null || true

echo "== record the build"
cat > /etc/arz/image.json <<JSON
{
  "built_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "vpc_subnet": "$VPC_SUBNET",
  "control_plane_ip": "$CONTROL_PLANE_IP",
  "runner_image_id": "$(docker image inspect --format '{{.Id}}' arz-runner:latest)",
  "vibkit": "$(docker run --rm --network none arz-runner:latest -c 'import vibkit; print(vibkit.__version__)')",
  "docker": "$(docker version --format '{{.Server.Version}}')",
  "runsc": "$(runsc --version | head -1)",
  "kernel": "$(uname -r)"
}
JSON
cat /etc/arz/image.json

echo "== scrub"
apt-get clean
journalctl --vacuum-time=1s >/dev/null 2>&1 || true
rm -rf /root/.bash_history /tmp/*
[[ "$SRC" == */arz-src ]] && rm -rf "$SRC"
rm -f /root/.ssh/authorized_keys
cloud-init clean --logs --seed --machine-id
sync
echo "BUILD_OK"
