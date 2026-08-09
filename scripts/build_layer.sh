#!/usr/bin/env bash
#
# Builds the Lambda dependency layer (PyYAML) for Lambda's platform.
# Run from the repo root BEFORE `terraform apply`:
#     ./scripts/build_layer.sh
#
# The layer/ directory is gitignored, so this regenerates it on a fresh clone.
# Pinning platform + python-version is required: a layer built for your local
# machine (e.g. Apple Silicon / newer Python) imports fine locally but fails at
# runtime on Lambda.
set -euo pipefail

LAYER_DIR="layer/python"

echo "Building Lambda dependency layer into ${LAYER_DIR} ..."
rm -rf layer
mkdir -p "${LAYER_DIR}"

pip install \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all: \
  --target "${LAYER_DIR}" \
  pyyaml

echo "Done. Layer ready at ${LAYER_DIR}. You can now run: terraform apply"