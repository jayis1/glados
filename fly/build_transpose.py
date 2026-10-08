#!/usr/bin/env python3
"""IST-269 / GLaDOS layer 1: cache the *transposed* connectome CSR.

    . env.sh
    python3 build_transpose.py

The LIF step needs current per *postsynaptic* neuron, so the matrix it actually
multiplies is A-transpose (rows = post, cols = pre). bench.py transposed on the
GPU at startup, which is fine for one device but wrong for two: each GPU must
hold a *row slice* of A-transpose, and you cannot slice what you have not
transposed yet without giving one card the whole graph first.

So transpose once, here, and write it to disk. Side effects that matter:

  * moodd.py starts without ever materialising the full graph on any single
    card, which is what makes the row split a real split rather than a copy.
  * the row split can be balanced by *nnz*, not by row count. Postsynaptic
    degree in this connectome is wildly skewed (max row = 11,475 edges, mean
    149), and SpMV cost follows nnz, so an even row-count split would leave one
    T400 waiting on the other.

Transpose is done on GPU 1 via cusparse because a 24.5M-element host-side
counting sort is ~20x slower and this runs on a card that is idle anyway.
Written as float16 for the same reason build_csr.py did: every value is an
integer weight times a sign, |max| = 2592, and fp16 is exact there.
"""
import os
import time

import cupy as cp
import numpy as np
from cupyx.scipy.sparse import csr_matrix

DATA = os.environ.get("GLADOS_DATA",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")).rstrip("/") + "/"
SRC = DATA + "csr_traced_signed.npz"
DST = DATA + "csr_t_signed.npz"
BUILD_GPU = 1


def main():
    t0 = time.time()
    z = np.load(SRC)
    n = len(z["indptr"]) - 1
    print(f"loaded {n:,} neurons  {len(z['indices']):,} edges  "
          f"{time.time() - t0:.1f}s")

    with cp.cuda.Device(BUILD_GPU):
        a = csr_matrix(
            (cp.asarray(z["data"].astype(np.float32)),
             cp.asarray(z["indices"]), cp.asarray(z["indptr"])),
            shape=(n, n),
        )
        t1 = time.time()
        at = a.T.tocsr()
        at.sort_indices()
        cp.cuda.Stream.null.synchronize()
        print(f"transposed on GPU{BUILD_GPU} in {time.time() - t1:.1f}s")
        indptr = cp.asnumpy(at.indptr).astype(np.int32)
        indices = cp.asnumpy(at.indices).astype(np.int32)
        data = cp.asnumpy(at.data).astype(np.float16)
        del a, at
        cp.get_default_memory_pool().free_all_blocks()

    deg = np.diff(indptr)
    print(f"post-degree  mean {deg.mean():.1f}  max {deg.max():,}  "
          f"zero-in {(deg == 0).sum():,}")
    np.savez(DST, indptr=indptr, indices=indices, data=data,
             shape=np.array([n, n], dtype=np.int64))
    print(f"wrote {DST}  total {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
