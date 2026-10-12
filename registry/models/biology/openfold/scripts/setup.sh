#!/bin/bash
#SBATCH -J openfold-setup
#SBATCH -p ice-cpu
#SBATCH -N 1 -n 1 -c 8
#SBATCH --mem=64G
#SBATCH -t 04:00:00
#SBATCH -o results/logs/%x-%j.out
# CPU job: env, OpenFold build, weights, inputs (all under OF_ROOT).
# - Stages skip when their output exists; resubmit after a failure.
# - Usage (from registry/models/biology/openfold): sbatch scripts/setup.sh
set -eo pipefail
source scripts/config.sh
mkdir -p "$OF_ROOT"

# 1. Miniforge
if [ ! -x "$OF_ROOT/miniforge3/bin/conda" ]; then
    wget -q -O "$OF_ROOT/miniforge.sh" \
        "https://github.com/conda-forge/miniforge/releases/download/$MINIFORGE_VERSION/Miniforge3-$MINIFORGE_VERSION-Linux-x86_64.sh"
    bash "$OF_ROOT/miniforge.sh" -b -p "$OF_ROOT/miniforge3" && rm "$OF_ROOT/miniforge.sh"
fi

# 2. OpenFold at the pinned commit
[ -d "$OF_REPO/.git" ] || git clone https://github.com/aqlaboratory/openfold.git "$OF_REPO"
git -C "$OF_REPO" checkout -q "$OF_COMMIT"

# 3. Conda env + upstream installer (resources, CUDA kernel)
[ -d "$OF_ENV" ] || "$OF_ROOT/miniforge3/bin/conda" env create -q -p "$OF_ENV" -f environment.yml
activate_env
if ! python -c "import torch, attn_core_inplace_cuda" 2>/dev/null; then
    (cd "$OF_REPO" && bash scripts/install_third_party_dependencies.sh)
    python -c "import torch, attn_core_inplace_cuda"  # installer hides build failures
fi

# 4. Weights via upstream scripts; checked against results/weights.sha256
[ -f "$OF_PARAMS/openfold_params/finetuning_ptm_2.pt" ] || bash "$OF_REPO/scripts/download_openfold_params.sh" "$OF_PARAMS"
[ -f "$OF_PARAMS/params/params_model_1_ptm.npz" ] || bash "$OF_REPO/scripts/download_alphafold_params.sh" "$OF_PARAMS"
SUMS=$PWD/results/weights.sha256
if [ -f "$SUMS" ]; then
    (cd "$OF_PARAMS" && sha256sum -c "$SUMS")
else
    (cd "$OF_PARAMS" && sha256sum openfold_params/finetuning_ptm_2.pt openfold_params/finetuning_no_templ_ptm_1.pt \
        params/params_model_1_ptm.npz params/params_model_3_ptm.npz) > "$SUMS"
fi

# 5. Inputs: CAMEO targets, alignments, mmCIFs
python scripts/prepare_data.py

echo "openfold $(git -C "$OF_REPO" describe --tags) ($OF_COMMIT)"
python -c "import sys, torch; print('python', sys.version.split()[0], '| torch', torch.__version__, '| cuda', torch.version.cuda)"
nvcc --version | tail -1
