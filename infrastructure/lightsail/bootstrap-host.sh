#!/usr/bin/env bash
# One-time preparation of a fresh Ubuntu (22.04 / 24.04) server, e.g. an AWS Lightsail instance:
# installs Docker (from Docker's official apt repository), adds swap and caps container log size.
#
#   sudo bash infrastructure/lightsail/bootstrap-host.sh
#
# Safe to run again. Afterwards log out and back in once, so your user can run `docker`.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this with sudo:  sudo bash $0" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
login_user="${SUDO_USER:-ubuntu}"

echo "==> Installing base packages"
apt-get update -y
apt-get install -y ca-certificates curl gnupg git python3 python3-venv unzip

echo "==> Installing Docker Engine + Compose plugin"
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  codename="$(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")"
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${codename} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

echo "==> Capping container log size (the disk is small)"
if [ ! -f /etc/docker/daemon.json ]; then
  mkdir -p /etc/docker
  cat > /etc/docker/daemon.json <<'JSON'
{ "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "3" } }
JSON
fi
systemctl enable --now docker
systemctl restart docker

echo "==> Adding 4 GB of swap (the embedding and reranker models need headroom on a 4 GB machine)"
if ! swapon --show=NAME --noheadings | grep -q '^/swapfile'; then
  fallocate -l 4G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "==> Letting '${login_user}' run docker without sudo"
usermod -aG docker "${login_user}"

docker --version
docker compose version
free -h | sed -n '1,3p'
echo
echo "Done. Log out and back in once (so the docker group applies), then run:"
echo "  bash infrastructure/lightsail/deploy.sh <your-hostname>"
