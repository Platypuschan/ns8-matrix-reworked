#!/bin/bash

# SPDX-License-Identifier: GPL-3.0-or-later

# Update the last published release of this module to the image under test,
# the same path an NS8 Software Center update takes.

set -Eeuo pipefail

cd "$(dirname "$0")"
IMAGE_URL="${2:?missing module image URL}"
status=0
PREVIOUS_IMAGE_URL="$(python3 .github/scripts/previous-release "${IMAGE_URL%:*}")" || status=$?
if [[ "${status}" -eq 3 ]]; then
    # Before the first release there is nothing to update from. Any other
    # lookup failure (registry, network) still fails the scenario.
    echo "::notice::No published release of ${IMAGE_URL%:*} yet; the update scenario is skipped."
    exit 0
elif [[ "${status}" -ne 0 ]]; then
    exit "${status}"
fi
echo "Update scenario: ${PREVIOUS_IMAGE_URL} -> ${IMAGE_URL}"
export PREVIOUS_IMAGE_URL

exec bash ./test-module.sh \
    "${1:?missing leader node address}" \
    "${IMAGE_URL}" \
    update
