#! /bin/bash -ex
cd "$(dirname "${0}")"
./base/build.sh
docker images
./build/build.sh
./selftests/build.sh
