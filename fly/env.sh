# Source this before anything that imports CuPy.  . env.sh
#
# CuPy JITs kernels at runtime and therefore needs CUDA *headers*, which a
# plain driver install does not ship. The pip CUDA wheels do. So CUDA_PATH is
# pointed at a shim directory whose include/ symlinks into the wheel's headers,
# and the wheel's shared libraries are put on the loader path.
#
# This is step zero and skipping it produces a JIT failure that blames
# something else entirely.
#
# Override SITE if you are not using a venv next to this script.

_here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
SITE="${SITE:-$(python3 -c 'import site; print(site.getsitepackages()[0])' 2>/dev/null)}"
NV="$SITE/nvidia"

if [ ! -d "$NV" ]; then
  echo "env.sh: no nvidia wheel libraries under $NV" >&2
  echo "        pip install cupy-cuda12x nvidia-cuda-runtime-cu12 nvidia-cusparse-cu12 \\" >&2
  echo "                    nvidia-cublas-cu12 nvidia-cuda-nvrtc-cu12 nvidia-nvjitlink-cu12" >&2
  echo "        or set SITE=/path/to/site-packages before sourcing." >&2
else
  # The shim: CUDA_PATH must be a directory containing include/.
  mkdir -p "$_here/cuda-shim"
  [ -e "$_here/cuda-shim/include" ] || ln -s "$NV/cuda_runtime/include" "$_here/cuda-shim/include"
  export CUDA_PATH="$_here/cuda-shim"
  export LD_LIBRARY_PATH="$NV/nvjitlink/lib:$NV/cusparse/lib:$NV/cublas/lib:$NV/cuda_runtime/lib:$NV/cuda_nvrtc/lib:${LD_LIBRARY_PATH:-}"
fi

# Where the connectome and derived matrices live. fetch_connectome.sh fills it.
export GLADOS_DATA="${GLADOS_DATA:-$_here/data}"
unset _here NV
