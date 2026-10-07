#!/usr/bin/env bash
# Only run inside the dedicated, newly created HacknexTest Ubuntu distribution.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
if [ "$ID" != "ubuntu" ]; then
  echo "This setup script supports Ubuntu only." >&2
  exit 1
fi
printf 'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' "$VERSION_CODENAME" "$(dpkg --print-architecture)" > /etc/apt/sources.list.d/docker.sources
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
if command -v systemctl >/dev/null && [ "$(ps -p 1 -o comm=)" = "systemd" ]; then
  systemctl enable --now docker
else
  service docker start
fi
docker version
docker run --rm hello-world
