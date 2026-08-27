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

if [ "${IMG}" != "nipa-base" ]; then
	ARGS+=(--cache-from "type=registry,ref=nipa-base")
fi

docker buildx build \
	--cache-to type=inline \
	-f "Dockerfile" --load \
	-t "${IMG}" "${ARGS[@]}" "${@}" .
docker tag "${IMG}" "${PUB_IMG}"
docker system prune --filter "label=name=${IMG}" -f >&2
