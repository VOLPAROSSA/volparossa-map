#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
set -euo pipefail
source_dir=$1
commit=$(python3 -c 'import json; print(json.load(open("upstream.lock.json"))["commit"])')
[[ $commit =~ ^[0-9a-f]{40}$ ]]
git init -q "$source_dir"
git -C "$source_dir" remote add origin https://github.com/organicmaps/organicmaps.git
git -C "$source_dir" fetch --depth 200 --filter=blob:none origin "$commit"
git -C "$source_dir" sparse-checkout init --cone
git -C "$source_dir" sparse-checkout set android cmake dev_sandbox .github 3party libs packaging generator qt tools \
    data/fonts data/symbols data/strings data/sound-strings data/countries-strings data/vulkan_shaders data/styles
git -C "$source_dir" checkout --detach FETCH_HEAD
git -C "$source_dir" submodule update --init --recursive --depth 1 --jobs 5
[[ $(git -C "$source_dir" rev-parse HEAD) = "$commit" ]]
! git -C "$source_dir" submodule status | grep -Eq '^[-+U]'
