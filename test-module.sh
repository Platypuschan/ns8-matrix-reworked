#!/bin/bash

#
# Copyright (C) 2026 Nethesis S.r.l.
# SPDX-License-Identifier: GPL-3.0-or-later
#

set -Eeuo pipefail

LEADER_NODE="${1:?missing leader node address}"
IMAGE_URL="${2:?missing module image URL}"
SCENARIO="${3:?missing test scenario}"
PREVIOUS_IMAGE_URL="${PREVIOUS_IMAGE_URL:-}"
SSH_KEYFILE="${SSH_KEYFILE:-${HOME}/.ssh/id_ecdsa}"
RUNNER_IMAGE="ghcr.io/marketsquare/robotframework-browser/rfbrowser-stable:19.11.0"
CONTAINER_NAME="rf-matrix-${SCENARIO}"

case "${SCENARIO}" in
    install) ;;
    update)
        if [[ -z "${PREVIOUS_IMAGE_URL}" ]]; then
            echo "The update scenario needs PREVIOUS_IMAGE_URL; run test-module-update.sh." >&2
            exit 64
        fi
        ;;
    *)
        echo "Unsupported test scenario '${SCENARIO}'; expected install or update." >&2
        exit 64
        ;;
esac

if [[ ! -r "${SSH_KEYFILE}" ]]; then
    echo "SSH key is not readable: ${SSH_KEYFILE}" >&2
    exit 66
fi

SSH_PRIVATE_KEY="$(<"${SSH_KEYFILE}")"
export IMAGE_URL LEADER_NODE PREVIOUS_IMAGE_URL SCENARIO SSH_PRIVATE_KEY

cleanup() {
    podman rm --force "${CONTAINER_NAME}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

mkdir -p "tests/outputs/${SCENARIO}"

set +e
podman run --interactive \
    --name "${CONTAINER_NAME}" \
    --replace \
    --volume "${PWD}:/home/pwuser/ns8-module:z" \
    --env IMAGE_URL \
    --env PREVIOUS_IMAGE_URL \
    --env LEADER_NODE \
    --env SCENARIO \
    --env SSH_PRIVATE_KEY \
    "${RUNNER_IMAGE}" \
    bash -l -s <<'EOF'
set -Eeuo pipefail

umask 077
printf '%s\n' "${SSH_PRIVATE_KEY}" > /home/pwuser/ns8-key

pip install --quiet --disable-pip-version-check \
    robotframework-sshlibrary==3.8.0

cd /home/pwuser/ns8-module
exec robot \
    -v "NODE_ADDR:${LEADER_NODE}" \
    -v "IMAGE_URL:${IMAGE_URL}" \
    -v "PREVIOUS_IMAGE_URL:${PREVIOUS_IMAGE_URL}" \
    -v "SSH_KEYFILE:/home/pwuser/ns8-key" \
    -v "SCENARIO:${SCENARIO}" \
    --name "matrix-${SCENARIO}" \
    --outputdir /home/pwuser/outputs \
    tests/
EOF
test_status=$?
set -e

podman cp \
    "${CONTAINER_NAME}:/home/pwuser/outputs/." \
    "tests/outputs/${SCENARIO}/" 2>/dev/null || true

exit "${test_status}"
