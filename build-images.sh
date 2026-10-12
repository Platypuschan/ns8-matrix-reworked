#!/bin/bash

#
# Copyright (C) 2023 Nethesis S.r.l.
# SPDX-License-Identifier: GPL-3.0-or-later
#

# Terminate on error
set -e

# Prepare variables for later use
images=()
# The image will be pushed to GitHub container registry
repobase="${REPOBASE:-ghcr.io/platypuschan}"
# Keep the published image name aligned with module-info.yml, which derives
# "matrix-reworked" from the repository name "ns8-matrix-reworked".
reponame="matrix-reworked"
repository_source="${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY:-Platypuschan/ns8-matrix-reworked}"
# Keep in sync with the Node.js version used by .github/workflows/validate.yml
NODE_IMAGE="docker.io/library/node:24.20.0"
nodebuilder="nodebuilder-matrix-${NODE_IMAGE##*:}"

# Images pulled with the module. NS8 passes each one to the module as
# <NAME>_IMAGE (POSTGRES_IMAGE, SYNAPSE_IMAGE, ELEMENT_WEB_IMAGE, ...).
# The bridge images are pulled only when a bridge is enabled; they are
# pinned in imageroot/pypkg/matrix_setup.py (BRIDGES).
module_images=(
	docker.io/library/postgres:17.11-alpine
	ghcr.io/element-hq/synapse:v1.162.0
	ghcr.io/element-hq/element-web:v1.12.30
	ghcr.io/cinnyapp/cinny:v4.12.7
	ghcr.io/etkecc/ketesa:v1.5.1
	docker.io/coturn/coturn:4.18.0-alpine
	docker.io/livekit/livekit-server:v1.13.7
	ghcr.io/element-hq/lk-jwt-service:0.7.0
)

# Create a new empty container image
container=$(buildah from scratch)

# Reuse an existing builder container for the same Node.js version, to speed up builds
if ! buildah containers --format "{{.ContainerName}}" | grep -qx "${nodebuilder}"; then
	echo "Pulling NodeJS runtime..."
	buildah from --name "${nodebuilder}" -v "${PWD}:/usr/src:Z" "${NODE_IMAGE}"
fi

echo "Build static UI files with node..."
buildah run \
	--workingdir=/usr/src/ui \
	"${nodebuilder}" \
	sh -c "corepack enable && yarn install --immutable && yarn build"

# Add imageroot directory to the container image
buildah add "${container}" imageroot /imageroot
buildah add "${container}" ui/dist /ui
# 20 TCP ports: Synapse, PostgreSQL, three web apps, ten bridges, the
# well-known service and three for Element Call (see PORT_NAMES in
# matrix_setup.py). 64 UDP ports for coturn and LiveKit (UDP_PORTS_DEMAND).
# node:fwadm opens the call ports; node:portsadm lets instances installed
# before 0.2.0 allocate their UDP ports.
# IMAGETAG overrides the published module tag (latest by default).
buildah config --entrypoint=/ \
	--label="org.opencontainers.image.source=${repository_source}" \
	--label="org.opencontainers.image.revision=${GITHUB_SHA:-$(git rev-parse HEAD 2>/dev/null || echo unknown)}" \
	--label="org.nethserver.authorizations=traefik@node:routeadm cluster:accountconsumer node:fwadm,portsadm" \
	--label="org.nethserver.tcp-ports-demand=20" \
	--label="org.nethserver.udp-ports-demand=64" \
	--label="org.nethserver.rootfull=0" \
	--label="org.nethserver.images=${module_images[*]}" \
	"${container}"
# Commit the image
buildah commit "${container}" "${repobase}/${reponame}"

# Append the image URL to the images array
images+=("${repobase}/${reponame}")

#
# Setup CI when pushing to Github.
# Warning! docker::// protocol expects lowercase letters (,,)
if [[ -n "${CI}" ]]; then
	# Set output value for Github Actions
	printf "images=%s\n" "${images[*],,}" >>"${GITHUB_OUTPUT}"
else
	# Just print info for manual push
	printf "Publish the images with:\n\n"
	for image in "${images[@],,}"; do printf "  buildah push %s docker://%s:%s\n" "${image}" "${image}" "${IMAGETAG:-latest}"; done
	printf "\n"
fi
