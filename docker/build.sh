#! /bin/bash -e
cd "$(dirname "${0}")"
./base/build.sh
./build/build.sh
