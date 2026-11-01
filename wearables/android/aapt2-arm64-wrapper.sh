#!/bin/sh
exec qemu-x86_64 -L /opt/x86 /android-sdk/build-tools/35.0.0/aapt2 "$@"
