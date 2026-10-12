# Shared settings for the OpenFold ICE workflow, sourced by setup.sh and run_inference.sh.
# Large artifacts (env, weights, inputs, predictions) live on scratch, outside the git repo.
export OF_ROOT=${OF_ROOT:-$HOME/scratch/openfold-repro}
export OF_REPO=$OF_ROOT/openfold      # upstream source, checked out at OF_COMMIT
export OF_ENV=$OF_ROOT/env            # conda env prefix (built from ../environment.yml)
export OF_PARAMS=$OF_ROOT/params      # openfold_params/*.pt and params/params_model_*.npz
export OF_DATA=$OF_ROOT/data          # FASTA, alignments, mmCIFs staged by prepare_data.py
export OF_RUNS=$OF_ROOT/runs          # predictions, one directory per weight set
export OF_COMMIT=e938c184a291bf053af3b14c1e3e8bb29aee57e2   # OpenFold tag v2.2.0
export MINIFORGE_VERSION=26.5.3-0
export MAX_TEMPLATE_DATE=2021-10-10   # template cutoff used to train OpenFold
# Keep package caches off the 30 GB home quota
export CONDA_PKGS_DIRS=$OF_ROOT/miniforge3/pkgs PIP_CACHE_DIR=$OF_ROOT/cache/pip TRITON_CACHE_DIR=$OF_ROOT/cache/triton

activate_env() {
    # PACE's default gcc module exports GCC_ROOT, which makes conda's g++ look for cc1plus in the
    # system gcc tree ("cannot execute 'cc1plus'") and breaks the CUDA extension build
    unset GCC_ROOT
    source "$OF_ROOT/miniforge3/etc/profile.d/conda.sh"
    conda activate "$OF_ENV"
    export LD_LIBRARY_PATH=$CONDA_PREFIX/lib:$LD_LIBRARY_PATH LIBRARY_PATH=$CONDA_PREFIX/lib:$LIBRARY_PATH
}
