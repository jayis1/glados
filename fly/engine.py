#!/usr/bin/env python3
"""IST-269 / GLaDOS layer 1: the connectome simulation engine.

A spiking LIF simulation of the Janelia male-CNS *Drosophila* connectome
(164,587 neurons, 24,539,704 sign-resolved edges), sharded by postsynaptic row
range across one or more GPUs, with named sensory injection sites and named
population readouts.

    . env.sh        # REQUIRED, see below
    python3 -c "
    import engine; s = engine.Connectome(devices=(1, 2)); print(s.describe())"

env.sh is not optional. cupy JIT-compiles every elementwise kernel through
NVRTC, so it needs CUDA *headers* on CUDA_PATH, not just the runtime libraries
the speech stack needs. Without it the device opens and reports its name and
then the first kernel dies with "Failed to find CUDA headers" - so a device
that opens is not evidence the stack works.

Sharding
--------
The step computes current per postsynaptic neuron, i.e. a SpMV against
A-transpose. Shard it by *row* range: shard k owns rows [r0, r1) of
A-transpose, which means it owns the membrane state, the refractory counters
and the spike output of exactly those neurons, and no partial sums ever need
adding - a row split partitions the output vector instead of replicating it.

What does have to be exchanged is the *input*: every shard's SpMV reads the
whole presynaptic spike vector. That is 164,587 floats = 658 kB per step
regardless of shard count, moved host-side through pinned buffers.

Row ranges are cut on cumulative nnz, not on row count. Postsynaptic degree
here runs from 0 to 11,475 with a mean of 149, and SpMV cost follows nnz, so an
even row-count split would leave one card idle waiting for the other.

Dynamics
--------
Leaky integrate-and-fire, dt = 1 ms, threshold 1.0, hard reset, 2 ms
refractory (so the ceiling is 1000/(1+2) = 333 Hz):

    v += -v/TAU + GAIN*cur + BIAS + drive + noise

with every term in threshold units per ms, so GAIN scales only the recurrent
graph and BIAS/drive/noise are independent of it.

`cur` is the signed SpMV against a **row-normalised** A-transpose: each
postsynaptic row is divided by its own total |weight|, so synaptic input is a
weighted average of presynaptic activity rather than a raw sum.

That normalisation is not cosmetic, and it is the single thing that makes this
simulation usable. Measured total input weight per neuron in the traced graph:

    population        in-deg   exc sum   inh sum       net
    whole network      149.1     446.4    -287.5    +158.9
    MBON               837.5    5689.9   -1046.9   +4643.0
    DAN                558.5    1140.9    -205.4    +935.5
    kenyon_cell        170.6     387.0     -60.5    +326.4
    central_complex    165.7     459.3    -573.0    -113.6

Un-normalised, MBON receives 29x the network's mean net excitation, so there is
*no* gain at which the network is alive and MBON is not pinned at 333 Hz. A
sweep of gain x bias x sigma over the raw graph produced 21 "usable" operating
points by whole-network rate and every one of them had kenyon_cell, MBON and
DAN at the refractory ceiling - the mood vector would have been a constant
maximum on three of its five axes while the network-level numbers looked fine.
Dividing by in-weight is standard homeostatic synaptic scaling and brings
MBON's net drive to 3x the mean instead of 29x.

Two external terms exist only to make the thing a sensor substrate rather than
a silent one:

  BIAS    constant depolarisation. With pure noise and no bias the network
          falls silent in ~20 steps and the mood vector is a constant zero -
          the boring half of the known failure mode.
  SIGMA   per-neuron noise. Without it the 2,635 identical ORNs under a
          constant injected current fire in exact lockstep and drag the network
          into one population-wide oscillation; the readouts then report the
          oscillation, not the stimulus.

GAIN/BIAS/SIGMA are not a priori values - they were swept against measured
output, see tune.py and MOOD.md.

The operating point is narrow and it is not where a liveness check would put
it. Measured, inh_scale 1.553, bias 0.025, driving all 2,635 ORNs at 0.1 -
mean |delta| across the five readouts, i.e. how much the mood vector actually
moves (measurements/sweep-gain.log, sweep-gain2.log):

    gain  sigma   rest Hz  readout response       what the vector would do
    0.40  0.060      1.08         0.09 Hz         nothing: constant
    1.50  0.060      2.37         9.89 Hz         3 of 5 axes dead at rest
    2.00  0.100      8.20        36.61 Hz         <- all five alive, graded
    2.50  0.100     12.95         9.70 Hz         KC/MBON/DAN near ceiling
    3.00  0.100     15.15        19.63 Hz         same, worse

At gain 0.40 the recurrent term is ~5% of the bias and the connectome is
decorative - the network rate still rose 1.08 -> 2.87 Hz under stimulus, but
*all* of that rise was the driven ORNs themselves firing and none of it
propagated, which is exactly how a flat mood vector hides behind a healthy
network number. Above 2.0 the balanced network tips into the explosive branch
and kenyon_cell/MBON/DAN sit at 60-130 Hz before any stimulus arrives. The
usable band is a factor of ~1.25 wide, so sigma matters as much as gain: at
sigma 0.06 the same gains leave three readouts at 0.0-0.15 Hz at rest.
"""
import json
import os
import time

import numpy as np

DATA = os.environ.get("GLADOS_DATA",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")).rstrip("/") + "/"
CSR_T = DATA + "csr_t_signed.npz"
POPULATIONS = DATA + "populations.json"

DT = 1.0
TAU = 20.0
VTH = 1.0
REFRACTORY = 2

# Swept, not guessed, and settled on readout *responsiveness* rather than on
# whole-network rate - see the table above, tune.py and MOOD.md. This is the
# only point measured at which all five readouts are alive at rest and none is
# within 4x of the refractory ceiling.
GAIN = 2.00
BIAS = 0.025
SIGMA = 0.100
# Measured excitatory/inhibitory input-weight ratio of the whole graph:
# 446.4 / 287.5 = 1.553. Scaling inhibition by that puts the network's mean
# net recurrent drive at zero.
INH_SCALE = 1.553
# Target baseline spike fraction per ms that the homeostatic bias compensates
# for: 0.010 = 10 Hz.
P0 = 0.010

# Readouts, in the order the mood vector reports them.
READOUTS = ("central_complex", "kenyon_cell", "MBON", "DAN", "escape_GF")
SENSORS = ("olfactory_ORN", "photoreceptor", "hearing_JO",
           "mechanosensory", "gustatory", "hygro_thermo")
ALL = "_network"
READOUT_ROWS = READOUTS + (ALL,)


def _pinned(n, dtype=np.float32):
    import cupy as cp
    nbytes = int(n) * np.dtype(dtype).itemsize
    mem = cp.cuda.alloc_pinned_memory(nbytes)
    return np.frombuffer(mem, dtype, count=int(n))


_LIF_SRC = r"""
    // xorshift32 per neuron: one uniform deviate, no second kernel and no
    // curand state. Uniform rather than Gaussian noise is deliberate - all it
    // has to do is break the lockstep of identically-driven sensory neurons,
    // and a Gaussian costs an extra kernel for no measurable difference in the
    // readouts.
    unsigned int x = rstate;
    x ^= x << 13; x ^= x >> 17; x ^= x << 5;
    rstate = x;
    float u = (float)(x >> 8) * (1.0f / 16777216.0f) - 0.5f;
    // Homeostatic bias: subtract the recurrent drive this neuron would receive
    // if the whole network sat at the target rate p0, so every neuron starts
    // from the same distance to threshold whatever its anatomy. nr is the
    // signed row sum of the normalised weights, in [-1, 1]. Without this the
    // static excitability spread pins MBON/DAN/KC while leaving
    // central_complex and escape_GF stuck on the noise floor - measured flat
    // at 3.1-3.8 Hz under every stimulus up to full saturation of the input.
    float b = bias - gain * p0 * nr;
    // dt = 1 ms. gain scales ONLY the recurrent graph; bias, drive and noise
    // are in threshold units per ms so they do not move when gain is swept.
    v += -v / tau + gain * cur + b + drive + u * sigma * 3.4641016f;
    bool f = (v >= vth) && (ref <= 0);
    v = f ? 0.0f : v;
    ref = f ? refr : ref - 1;
    fired = f ? 1.0f : 0.0f;
"""


def _lif_kernel(cp):
    """One kernel for the whole membrane update.

    Written out as a fused kernel rather than the obvious six cupy expressions
    because at 4.7 ms of compute per card the *Python* launch overhead stops
    being free: the naive version issued ~12 launches per shard per step, and
    with two shards driven from one thread that serial overhead was 1.46 ms of
    a 6.16 ms step - i.e. most of the gap between the measured 1.53x and the
    ideal 2x. Fused, the 2-GPU step measures 4.575 ms against 8.889 ms on one
    card: 1.94x, overhead 0.131 ms/step. See measurements/bench-split.log.
    """
    return cp.ElementwiseKernel(
        "float32 cur, float32 drive, float32 nr, float32 bias, float32 gain, "
        "float32 p0, float32 sigma, float32 tau, float32 vth, int32 refr",
        "float32 v, int32 ref, float32 fired, uint32 rstate",
        _LIF_SRC, "glados_lif",
    )


def nnz_split(indptr, parts):
    """Row cut points equalising nnz, not row count."""
    n = len(indptr) - 1
    if parts <= 1:
        return [(0, n)]
    targets = [indptr[-1] * (k + 1) // parts for k in range(parts - 1)]
    cuts = [0] + [int(np.searchsorted(indptr, t)) for t in targets] + [n]
    cuts = sorted(set(cuts))
    while len(cuts) < parts + 1:               # pathological, but stay honest
        cuts.append(n)
    return [(cuts[k], cuts[k + 1]) for k in range(parts)]


class _Shard:
    """One GPU's row range: its slice of A-transpose and its own neurons."""

    def __init__(self, cp, csr_matrix, dev, r0, r1, z, n, pops, nr):
        self.dev = dev
        self.r0, self.r1 = r0, r1
        self.nloc = r1 - r0
        with cp.cuda.Device(dev):
            self.nr = cp.asarray(nr[r0:r1])
            lo, hi = int(z["indptr"][r0]), int(z["indptr"][r1])
            ip = (z["indptr"][r0:r1 + 1] - lo).astype(np.int32)
            self.nnz = hi - lo
            self.at = csr_matrix(
                (cp.asarray(z["data"][lo:hi].astype(np.float32)),
                 cp.asarray(z["indices"][lo:hi]),
                 cp.asarray(ip)),
                shape=(self.nloc, n),
            )
            self.spk = cp.zeros(n, dtype=cp.float32)        # full presyn input
            self.v = cp.zeros(self.nloc, dtype=cp.float32)
            self.ref = cp.zeros(self.nloc, dtype=cp.int32)
            self.drive = cp.zeros(self.nloc, dtype=cp.float32)
            self.fired = cp.zeros(self.nloc, dtype=cp.float32)
            self.host_seg = _pinned(self.nloc)
            seed = np.random.default_rng(1234 + dev).integers(
                1, 2 ** 32 - 1, size=self.nloc, dtype=np.uint32)
            self.rstate = cp.asarray(seed)

            # Sensory sites and readouts restricted to this shard's rows.
            self.sensor_idx = {}
            for name in SENSORS:
                idx = np.asarray(pops.get(name, ()), dtype=np.int64)
                idx = idx[(idx >= r0) & (idx < r1)] - r0
                if len(idx):
                    self.sensor_idx[name] = cp.asarray(idx)

            # One tiny one-hot CSR so a readout is a single P-row SpMV instead
            # of P separate reductions per step. The last row is every neuron
            # on the shard, which makes the whole-network mean rate - the
            # saturation check - cost nothing extra.
            rows, cols = [], []
            for i, name in enumerate(READOUT_ROWS):
                if name == ALL:
                    idx = np.arange(self.nloc, dtype=np.int64)
                else:
                    idx = np.asarray(pops.get(name, ()), dtype=np.int64)
                    idx = idx[(idx >= r0) & (idx < r1)] - r0
                rows.append(np.full(len(idx), i, dtype=np.int32))
                cols.append(idx.astype(np.int32))
            rows = np.concatenate(rows)
            cols = np.concatenate(cols)
            order = np.argsort(rows, kind="stable")
            rows, cols = rows[order], cols[order]
            rptr = np.zeros(len(READOUT_ROWS) + 1, dtype=np.int32)
            np.cumsum(np.bincount(rows, minlength=len(READOUT_ROWS)),
                      out=rptr[1:])
            self.readout = csr_matrix(
                (cp.ones(len(cols), dtype=cp.float32),
                 cp.asarray(cols), cp.asarray(rptr)),
                shape=(len(READOUT_ROWS), self.nloc),
            )
            self.total = cp.zeros(len(READOUT_ROWS), dtype=cp.float32)
            self.lif = _lif_kernel(cp)


class Connectome:
    """The sharded simulation. Not thread-safe; moodd owns one from one thread.

    `devices=()` runs nothing and is how the service degrades: it reports
    unavailable rather than falling back to the CPU, which measured 352 ms/step
    - 0.003x realtime, so a CPU "fallback" would produce a mood vector lagging
    reality by minutes and would be worse than no answer.
    """

    @staticmethod
    def _normalise(z, norm, inh_scale):
        """Scale inhibition by `inh_scale`, then divide each postsynaptic row
        by its own total |weight|.

        float32, not the fp16 on disk: a row sum of 6,738 (MBON's) has no fp16
        headroom, and the normalised weights land near 1e-4 where fp16's 6e-5
        resolution would quantise most of the graph to a handful of values.

        `inh_scale` is the second thing the measurements forced. Row
        normalisation alone leaves the graph net-excitatory (mean net ratio
        +0.22), so the only gains that do not run away are gains at which the
        recurrent term is ~5% of the bias - measured: holding bias and sigma
        and sweeping gain 0.1 -> 0.8 moved the network rate 12.41 -> 12.72 Hz,
        i.e. the connectome was decorative and the mood vector was reading
        noise. Scaling inhibition to put the network's *mean* net drive at zero
        makes firing fluctuation-driven and therefore input-sensitive, while
        still leaving MBON net-excitatory and central_complex net-inhibitory -
        the population asymmetries are real anatomy and are preserved.
        """
        data = z["data"].astype(np.float32)
        if inh_scale != 1.0:
            np.multiply(data, inh_scale, out=data, where=data < 0)
        if norm == "none":
            return data
        if norm != "row":
            raise ValueError(f"norm={norm!r}")
        n = len(z["indptr"]) - 1
        rows = np.repeat(np.arange(n, dtype=np.int32), np.diff(z["indptr"]))
        tot = np.bincount(rows, weights=np.abs(data), minlength=n)
        tot[tot == 0] = 1.0                       # 144 rows have no input
        return (data / tot[rows]).astype(np.float32)

    def __init__(self, devices=(1, 2), gain=GAIN, bias=BIAS, sigma=SIGMA,
                 p0=P0, norm="row", inh_scale=None, seed=None):
        import cupy as cp
        from cupyx.scipy.sparse import csr_matrix
        self.cp = cp
        self.gain, self.bias, self.sigma = float(gain), float(bias), float(sigma)
        self.p0 = float(p0)
        self.devices = tuple(int(d) for d in devices)
        if not self.devices:
            raise ValueError("no devices")

        t0 = time.time()
        # Materialise once: npz members decompress on every __getitem__, and a
        # per-shard z["data"][lo:hi] would re-read the whole 24.5M array per
        # card.
        with np.load(CSR_T) as zf:
            z = {k: zf[k] for k in ("indptr", "indices", "data")}
            self.n = int(zf["shape"][0])
        self.nnz = int(len(z["indices"]))
        self.norm = norm
        self.inh_scale = INH_SCALE if inh_scale is None else float(inh_scale)
        z["data"] = self._normalise(z, norm, self.inh_scale)
        rows = np.repeat(np.arange(self.n, dtype=np.int32),
                         np.diff(z["indptr"]))
        self.netratio = np.bincount(rows, weights=z["data"],
                                    minlength=self.n).astype(np.float32)
        del rows
        self.pops = json.load(open(POPULATIONS))
        self.shards = []
        for dev, (r0, r1) in zip(self.devices,
                                 nnz_split(z["indptr"], len(self.devices))):
            self.shards.append(_Shard(cp, csr_matrix, dev, r0, r1,
                                      z, self.n, self.pops, self.netratio))
        del z
        self.gpu_names = {}
        for d in self.devices:
            with cp.cuda.Device(d):
                self.gpu_names[d] = cp.cuda.runtime.getDeviceProperties(
                    d)["name"].decode()
        self.exchange = self._enable_peer()
        self._sizes = np.array(
            [len(self.pops.get(k, ())) if k != ALL else self.n
             for k in READOUT_ROWS], dtype=np.float64)
        self._sizes[self._sizes == 0] = 1.0
        self.load_s = time.time() - t0
        self.steps = 0
        self._last_counts = np.zeros(len(READOUT_ROWS))
        self._last_steps = 0
        self.drive = {name: 0.0 for name in SENSORS}
        if seed is not None:
            for s in self.shards:
                with cp.cuda.Device(s.dev):
                    rs = np.random.default_rng(seed + s.dev).integers(
                        1, 2 ** 32 - 1, size=s.nloc, dtype=np.uint32)
                    s.rstate = cp.asarray(rs)

    # ---- sensory input -------------------------------------------------
    def set_drive(self, **rates):
        """Injected current into named sensory populations, in VTH units/ms.

        0 is silence. ~0.05 is a clear stimulus, ~0.3 saturates the site
        itself. Unknown names raise - a typo that silently injected nothing
        would look exactly like a network that does not respond.
        """
        cp = self.cp
        for name, val in rates.items():
            if name not in SENSORS:
                raise KeyError(name)
            self.drive[name] = float(val)
        for s in self.shards:
            with cp.cuda.Device(s.dev):
                s.drive[:] = 0.0
                for name, idx in s.sensor_idx.items():
                    if self.drive[name]:
                        s.drive[idx] = self.drive[name]

    # ---- the step ------------------------------------------------------
    def _enable_peer(self):
        """Try direct T400<->T400 copies. Measured 0.075 ms for the full
        164,587-float vector versus 0.101 ms through a pinned host buffer, so
        the win is small - the exchange was never the bottleneck - but it also
        removes two host syncs per step, which is not small."""
        cp = self.cp
        if len(self.devices) < 2:
            return "local"
        try:
            for a in self.devices:
                cp.cuda.Device(a).use()
                for b in self.devices:
                    if a == b:
                        continue
                    if not cp.cuda.runtime.deviceCanAccessPeer(a, b):
                        return "pinned_host"
                    try:
                        cp.cuda.runtime.deviceEnablePeerAccess(b)
                    except Exception:             # noqa: BLE001
                        pass                      # already enabled
            return "peer"
        except Exception:                         # noqa: BLE001
            return "pinned_host"

    def step(self, k=1):
        """Advance k ms.

        Every shard's compute is *launched* before any shard is waited on, so
        the cards overlap; the spike exchange at the end of each step is the
        only serial part.
        """
        cp = self.cp
        shards = self.shards
        peer = self.exchange == "peer"
        for _ in range(k):
            for s in shards:                      # launch, do not sync
                with cp.cuda.Device(s.dev):
                    cur = s.at.dot(s.spk)
                    s.lif(cur, s.drive, s.nr, self.bias, self.gain, self.p0,
                          self.sigma, TAU, VTH, REFRACTORY,
                          s.v, s.ref, s.fired, s.rstate)
                    s.total += s.readout.dot(s.fired)
            if len(shards) == 1:
                s = shards[0]
                with cp.cuda.Device(s.dev):
                    s.spk[s.r0:s.r1] = s.fired
            elif peer:
                for src in shards:
                    with cp.cuda.Device(src.dev):
                        src.spk[src.r0:src.r1] = src.fired
                        for dst in shards:
                            if dst is src:
                                continue
                            cp.cuda.runtime.memcpyPeer(
                                dst.spk.data.ptr + src.r0 * 4, dst.dev,
                                src.fired.data.ptr, src.dev, src.nloc * 4)
            else:
                for s in shards:                  # barrier: both are running
                    with cp.cuda.Device(s.dev):
                        s.fired.get(out=s.host_seg)
                for dst in shards:
                    with cp.cuda.Device(dst.dev):
                        for src in shards:
                            if src is dst:
                                dst.spk[dst.r0:dst.r1] = dst.fired
                            else:
                                dst.spk[src.r0:src.r1].set(src.host_seg)
            self.steps += 1

    # ---- readout -------------------------------------------------------
    def counts(self):
        """Cumulative spike counts per readout row, summed over shards.

        Cumulative rather than a rate: a row split makes counts additive, and
        the only device sync in the whole step loop is this one fetch of six
        floats, which the caller pays only when it actually wants a reading.
        """
        cp = self.cp
        acc = np.zeros(len(READOUT_ROWS), dtype=np.float64)
        for s in self.shards:
            with cp.cuda.Device(s.dev):
                acc += cp.asnumpy(s.total).astype(np.float64)
        return acc

    def rates_hz(self):
        """Mean firing rate per readout, in Hz, over the window since the last
        call to this method. Includes `_network`, the saturation check."""
        c, n = self.counts(), self.steps
        dc, dn = c - self._last_counts, n - self._last_steps
        self._last_counts, self._last_steps = c, n
        if dn <= 0:
            return {k: 0.0 for k in READOUT_ROWS}
        hz = dc / self._sizes / (dn * DT / 1000.0)
        return dict(zip(READOUT_ROWS, hz))

    def mean_hz(self):
        return self.rates_hz()[ALL]

    def reset(self):
        cp = self.cp
        for s in self.shards:
            with cp.cuda.Device(s.dev):
                s.v[:] = 0.0
                s.ref[:] = 0
                s.spk[:] = 0.0
                s.total[:] = 0.0
                s.fired[:] = 0.0
        self.steps = 0
        self._last_counts = np.zeros(len(READOUT_ROWS))
        self._last_steps = 0

    def describe(self):
        return {
            "neurons": self.n,
            "edges": self.nnz,
            "devices": list(self.devices),
            "gpus": {str(k): v for k, v in self.gpu_names.items()},
            "exchange": self.exchange,
            "shards": [{"gpu": s.dev, "rows": [s.r0, s.r1], "nnz": s.nnz}
                       for s in self.shards],
            "dt_ms": DT, "tau_ms": TAU, "v_th": VTH,
            "refractory_ms": REFRACTORY,
            "gain": self.gain, "bias": self.bias, "sigma": self.sigma,
            "p0": self.p0, "norm": self.norm, "inh_scale": self.inh_scale,
            "ceiling_hz": 1000.0 / (1 + REFRACTORY),
            "load_s": round(self.load_s, 2),
            "readouts": {k: len(self.pops.get(k, ())) for k in READOUTS},
            "sensors": {k: len(self.pops.get(k, ())) for k in SENSORS},
        }


def available_devices():
    """T400s only. GPU 0 is the GTX 1660 SUPER and belongs to whisper+Kokoro."""
    want = os.environ.get("MOOD_GPUS")
    if want:
        return tuple(int(x) for x in want.replace(",", " ").split())
    try:
        import cupy as cp
        out = []
        for d in range(cp.cuda.runtime.getDeviceCount()):
            name = cp.cuda.runtime.getDeviceProperties(d)["name"].decode()
            if "T400" in name:
                out.append(d)
        return tuple(out)
    except Exception:                                         # noqa: BLE001
        return ()
