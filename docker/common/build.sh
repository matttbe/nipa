#!/bin/bash -e
# SPDX-License-Identifier: GPL-2.0

: "${INPUT_PUSH:=0}"
: "${INPUT_PREFIX:="ghcr.io/linux-netdev"}"
: "${INPUT_TAG:="latest"}"

cd "$(dirname "${0}")"

DIR_NAME="$(basename "${PWD}")"
ARGS=()
IMG="nipa-${DIR_NAME}"
PUB_IMG="${INPUT_PREFIX}/${IMG}:${INPUT_TAG}"

if [[ ${-} =~ "x" ]]; then
	ARGS+=(--progress plain)
fi

if [ "${INPUT_PUSH}" = 1 ]; then
	ARGS+=(--push)
fi

docker buildx build --build-arg=BASE_PREFIX="${INPUT_PREFIX}" \
	--build-arg=BASE_TAG="${INPUT_TAG}" -f "Dockerfile" --load \
	-t "${PUB_IMG}" "${ARGS[@]}" "${@}" .
docker system prune --filter "label=name=${IMG}" -f >&2
