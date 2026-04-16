#!/usr/bin/env python3
"""Standalone FEA solver subprocess — no Cura/plugin imports.

Invoked by fea_solver.py via subprocess.Popen to run spsolve or CG in a
separate process with its own GIL, preventing GIL starvation of the Qt
main thread.

Protocol:
    argv[1] = input .npz path  (K_data, K_indices, K_indptr, K_shape, f)
    argv[2] = output .npz path (u, info)
    argv[3] = solver type: "spsolve" or "cg"
"""

import sys

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla


def main():
    inp_path = sys.argv[1]
    out_path = sys.argv[2]
    solver = sys.argv[3] if len(sys.argv) > 3 else "spsolve"

    data = np.load(inp_path)
    K = sp.csr_matrix(
        (data["K_data"], data["K_indices"], data["K_indptr"]),
        shape=tuple(data["K_shape"]),
    )
    f = data["f"]

    if solver == "cg":
        diag = K.diagonal().copy()
        diag[diag == 0] = 1.0
        M_inv = sp.diags(1.0 / diag)
        u, info = spla.cg(K, f, M=M_inv, tol=1e-6, maxiter=2000)
        np.savez(out_path, u=u, info=np.array([int(info)]))
    else:
        u = spla.spsolve(K, f)
        np.savez(out_path, u=u, info=np.array([0]))


if __name__ == "__main__":
    main()
