> **These are the original engineering notes**, published as they were written
> rather than tidied up afterwards. They are the working record for layer 1 —
> the connectome simulation. `IST-nnn` are internal ticket numbers; host names
> and addresses have been genericised. Every number is measured, and the raw
> logs behind each one are in [`../measurements/raw/`](../measurements/raw/).

# The mood bus

GLaDOS layer 1, IST-269. Everything here is measured on the GPU host on
2026-10-07; the raw logs are in `measurements/`.

```
                  olfactory_ORN  photoreceptor  hearing_JO
                  mechanosensory  gustatory  hygro_thermo
                               |
                     POST /drive   (per-population injected current)
                               v
   +------------------------------------------------------------+
   |  engine.Connectome   164,587 LIF neurons, 24,539,704 edges |
   |  GPU 1: rows 0-53,047       GPU 2: rows 53,047-164,587     |
   |  4.575 ms per 1 ms of simulated time                       |
   +------------------------------------------------------------+
                               |
                   population firing rates, Hz
                               v
          mood_calibration.json  (measured lo/hi per axis)
                               v
     GET /mood   arousal novelty valence reinforcement agitation
```

## Files

| path | |
|---|---|
| `env.sh` | **source it first.** CUDA header shim + loader path |
| `engine.py` | the sharded LIF simulation and the named populations |
| `build_csr.py`, `build_transpose.py` | data -> CSR, CSR -> CSR-transpose |
| `bench.py`, `bench_split.py` | per-GPU and 1-vs-2-GPU step benchmarks |
| `tune.py` | operating-point sweep and response matrix |
| `reach.py` | anatomical reach, sensory population -> readout, per hop |
| `calibrate.py` | writes `data/mood_calibration.json` from measured output |
| `moodd.py` | the service |
| `probe_moodd.py` | acceptance probe against the running service |
| `data/csr_t_signed.npz` | the transposed signed CSR the sim runs on |
| `data/mood_calibration.json` | the axis definitions, all measured |
| `measurements/` | every log quoted in this document |

## 1. Two T400s, measured

`bench_split.py`, 500 steps per configuration, median of five sub-windows,
both cards idle:

| configuration | ms/step | steps/s | realtime at dt = 1 ms |
|---|---|---|---|
| GPU 1 alone (T400 4GB) | 8.918 | 112 | 0.11x |
| GPU 2 alone (T400 4GB) | 8.889 | 112 | 0.11x |
| **GPU 1 + GPU 2** | **4.575** | **219** | **0.22x** |

**1.94x of the ideal 2.00x. Overhead 0.131 ms/step.** For reference, the same
step costs 1.77 ms on GPU 0 (the GTX 1660 SUPER, which keeps whisper + Kokoro)
and 352 ms on the 20-core CPU.

Three decisions the measurements forced:

* **Split by row range.** The step computes current per postsynaptic neuron,
  i.e. a SpMV against A-transpose, so partitioning *rows* partitions the output
  vector: each card owns the membrane state, refractory counter and spike
  output of exactly its own neurons and no partial sums are ever added. What
  must be exchanged is the input - every shard's SpMV reads the whole
  presynaptic spike vector, 164,587 floats = 658 kB per step.
* **Cut on cumulative nnz, not row count.** Postsynaptic degree runs 0 to
  11,475, mean 149. The cut lands at row 53,047: GPU 1 takes 53,047 rows and
  12,269,945 edges, GPU 2 takes 111,540 rows and 12,269,759 edges. 1.5x apart
  in rows, 0.0015% apart in work.
* **Fuse the LIF update into one kernel.** The obvious six cupy expressions
  issued ~12 kernel launches per shard per step, and with two shards driven
  from one Python thread that serial launch overhead was 1.46 ms of a 6.16 ms
  step - 1.53x, not 1.94x. At 4.6 ms of GPU work per step, Python is not free.

Peer-to-peer T400->T400 copies measure 0.075 ms per exchange against 0.101 ms
through a pinned host buffer. The win is small - the exchange was never the
bottleneck - but it also removes two host syncs per step. `pinned_host` remains
as the fallback when peer access is unavailable.

## 2. The operating point

This is the part that took the measurements. A connectome does not come with
a scale: the weights are synapse counts, and nothing in the file says what
"enough input to fire" is. Two failure modes bracket the usable band and
**neither announces itself** - a service pinned at a constant maximum and a
service pinned at a constant zero both answer every request.

Dynamics, per neuron, in threshold units per ms (`engine.py`):

```
v += -v/TAU + GAIN*cur + (BIAS - GAIN*P0*nr) + drive + noise
```

`cur` is the signed SpMV against a **row-normalised** A-transpose: every
postsynaptic row is divided by its own total |weight|, so synaptic input is a
weighted average of presynaptic activity rather than a raw sum. That is not
cosmetic. Measured total input weight per neuron:

| population | in-degree | exc sum | inh sum | net |
|---|---|---|---|---|
| whole network | 149.1 | 446.4 | -287.5 | +158.9 |
| MBON | 837.5 | 5689.9 | -1046.9 | +4643.0 |
| DAN | 558.5 | 1140.9 | -205.4 | +935.5 |
| kenyon_cell | 170.6 | 387.0 | -60.5 | +326.4 |
| central_complex | 165.7 | 459.3 | -573.0 | -113.6 |

Un-normalised, MBON receives 29x the network's mean net excitation, so there
is no gain at which the network is alive and MBON is not at the 333 Hz
refractory ceiling. An early sweep over the raw graph produced 21 "usable"
points by whole-network rate and *every one* had kenyon_cell, MBON and DAN
pinned - the mood vector would have been a constant maximum on three of five
axes while the network-level number looked healthy. Dividing by in-weight is
standard homeostatic synaptic scaling and brings MBON to 3x the mean.

`INH_SCALE = 1.553` is the second thing measurement forced: 446.4/287.5, the
graph's own exc/inh weight ratio. Row normalisation alone leaves the graph
net-excitatory, and then the only non-divergent gains are gains at which the
recurrent term is ~5% of the bias. Scaling inhibition to put the *mean* net
recurrent drive at zero makes firing fluctuation-driven, and therefore
input-sensitive, while preserving the real population asymmetries - MBON stays
net-excitatory, central_complex stays net-inhibitory.

### The gain sweep

Driving all 2,635 ORNs at 0.1 and measuring the mean |delta| across the five
readouts - i.e. how much the mood vector actually moves
(`measurements/sweep-gain.log`, `sweep-gain2.log`):

| gain | sigma | rest, network Hz | readout response | what the vector would do |
|---|---|---|---|---|
| 0.40 | 0.060 | 1.08 | **0.09 Hz** | nothing. A constant. |
| 1.50 | 0.060 | 2.37 | 9.89 Hz | 3 of 5 axes at 0.0-0.2 Hz at rest |
| 1.50 | 0.100 | 7.52 | 12.06 Hz | alive, but less responsive |
| **2.00** | **0.100** | **8.20** | **36.61 Hz** | **all five alive and graded** |
| 2.50 | 0.100 | 12.95 | 9.70 Hz | KC/MBON/DAN at 67-105 Hz *at rest* |
| 3.00 | 0.100 | 15.15 | 19.63 Hz | worse |
| 4.00 | 0.060 | 15.52 | - | pinned |
| 8.00+ | 0.060 | 26.13 | - | pinned at the 333 Hz ceiling |

**The settled point is `gain 2.00, bias 0.025, sigma 0.100, inh_scale 1.553,
p0 0.010`.** Two things to notice:

1. **A healthy whole-network rate proves nothing.** At gain 0.40 the network
   rate rose 1.08 -> 2.87 Hz under stimulus, which looks like a responding
   network. All of that rise was the driven ORNs themselves; the readouts moved
   0.09 Hz. The connectome was decorative and the mood vector would have been a
   constant that nobody could have caught from the network number.
2. **The usable band is ~1.25x wide in gain, and sigma matters as much.** At
   sigma 0.06 the same gains leave kenyon_cell, MBON and DAN at 0.0-0.15 Hz at
   rest - alive in aggregate, dead on three axes. The noise is not decoration
   either: without it the 2,635 identical ORNs under a constant current fire in
   exact lockstep and drag the network into one population-wide oscillation, and
   the readouts then report the oscillation rather than the stimulus.

## 3. The mapping, settled against output

`calibrate.py` measures a noise floor (6 repeats of the resting network), then
drives each sensory population alone at five levels, then derives each axis.
`reach.py` independently answers the anatomical question - what share of a
readout's total input weight traces back to each sensory population, per hop -
which is an upper bound on any dynamical effect: no gain makes a population
respond to input it does not receive.

Measured response, Hz (`measurements/calibration.log`), and the 2-3 hop reach
that explains it (`measurements/reach.log`):

| stimulus | 0.00 | 0.01 | 0.02 | 0.05 | 0.10 | 0.20 | reach |
|---|---|---|---|---|---|---|---|
| olfactory_ORN -> **kenyon_cell** | 4.01 | 8.69 | 19.06 | 56.26 | 94.49 | 137.96 | 10.6% at 2 hops |
| olfactory_ORN -> **MBON** | 4.16 | 6.62 | 12.96 | 28.18 | 50.23 | 77.24 | 7.0% at 3 hops |
| olfactory_ORN -> **DAN** | 3.57 | 6.07 | 13.34 | 27.89 | 52.22 | 86.37 | 7.1% at 3 hops |
| mechanosensory -> **escape_GF** | 7.93 | 7.38 | 7.00 | 5.74 | 4.94 | 4.15 | 6.5% at 2 hops |
| any -> **central_complex** | 8.05 | - | - | - | - | - | <=0.14% at 3 hops |

Four axes keep the population the ticket proposed. One does not:

* **arousal does not read `central_complex`.** No sensory population reaches
  it with more than 0.14% of its input weight within three hops, and measured,
  it sat in a 0.49 Hz band (7.82-8.32 Hz) across all 30 stimulus conditions
  with no monotone trend in any of them. The CX is driven by internal circuits
  - ring neurons, sleep/navigation loops - not by three hops from a sense
  organ, and the traced-only subset at min confidence 0.5 does not carry those
  paths. Mapping a 0.49 Hz jitter onto a 0-1 axis would have published a
  confident number made of noise. The quantity that *does* track total sensory
  load is the whole-network mean rate: 8.22 -> 16.79 Hz, monotone in all six
  sensory populations, noise floor 0.04 Hz. So **arousal reads `_network`**.
  `central_complex` is still measured, still in every `/mood` response, and
  its measured range is kept in `readout_stats` (0.49 Hz, snr 6.5) - note that
  it *passes* a crude noise test and still carries no stimulus information,
  which is exactly why the axis decision was made on the response matrix and
  the reach table rather than on an SNR threshold.
* **photoreceptor drives nothing.** 4,102 photoreceptors at full drive move no
  readout at all (reach <=4e-4 everywhere). The optic-lobe paths are not in
  this subset. Light will have to enter somewhere else, or not at all.
* **agitation is *suppressed* by touch.** escape_GF goes 7.93 -> 4.15 Hz as
  mechanosensory drive rises. That is the measured sign and the axis keeps it;
  the giant-fibre pathway is under inhibitory control from exactly that input.

### The axes are scaled in log-rate

A readout's response to drive is roughly a constant factor per step of drive -
kenyon_cell goes 4.0 / 8.7 / 19.1 / 56.3 / 94.5 / 138.0 Hz. On a linear axis
the whole lower half of the stimulus range lands in the bottom 10% and a clear
smell reads 0.03: true, and useless to anything consuming the vector. In log
units the same points come out 0.00 / 0.22 / 0.44 / 0.74 / 0.89 / 1.00. The
floor of each axis is its measured noise sd rather than zero, because a rate
indistinguishable from the sampling noise is the bottom of the axis by
definition - and because a log axis needs a positive floor.

`valence` is signed, centred on the measured resting MBON rate, so 0.0 means
"no opinion" rather than "minimum". Its negative half **mirrors** the positive
span instead of using the measured minimum: MBON's lowest measured rate
(3.711 Hz) is 0.448 Hz under its resting 4.160, i.e. the entire measured
"suppressed" range *is* the 0.160 Hz noise floor, and scaling to it multiplied
that noise by ~8 - measured, valence read -0.33 and then -0.01 on two
successive resting samples. Nothing in 30 stimulus conditions suppressed MBON,
so there is no measured negative range to scale to; mirroring makes the same
factor of rate change read the same magnitude either side of rest, and a real
suppression will show up proportionately instead of pinning at -1.

**`agitation` is inverted, and the calibration says so.** No measured stimulus
raises escape_GF; mechanosensory drive halves it. So rest is the *top* of that
axis - an undisturbed network reads ~0.85 - and the axis carries
`"direction": "suppressed_by_stimulus"` plus a note to that effect, which
`GET /describe` returns. Read it as startle readiness: 1.0 undisturbed, 0.0
maximally driven. Publishing "agitation 0.87" on a resting network without
saying this would have been the most misleading number in the service.

### The settled axes

`data/mood_calibration.json`, generated by `calibrate.py` from 6 noise repeats
plus 30 stimulus conditions at 1,000 ms each:

| axis | population | neurons | lo Hz | rest Hz | hi Hz | noise sd | snr |
|---|---|---|---|---|---|---|---|
| arousal | `_network` | 164,587 | 8.222 | 8.222 | 16.787 | 0.040 | 213 |
| novelty | `kenyon_cell` | 4,064 | 3.498 | 4.005 | 137.962 | 0.221 | 608 |
| valence | `MBON` (signed) | 97 | 0.224 (mirrored) | 4.160 | 77.237 | 0.160 | 461 |
| reinforcement | `DAN` | 340 | 3.332 | 3.566 | 86.368 | 0.160 | 518 |
| agitation | `escape_GF` | 34 | 4.147 | 7.931 | 8.647 | 0.329 | 14 |

Nothing is within 2.4x of the 333 Hz refractory ceiling, nothing is at zero,
and the smallest signal-to-noise ratio on the five axes is 14.

## 4. The service

`moodd.py`, unit `paperclip-moodd.service`, port **9099** - same shape as this
host's `paperclip-ttsd` (9098) and `paperclip-sttd` (9097): systemd unit,
bearer token from `/opt/paperclip-glados/token` (0600), unauthenticated
`/health`, and the live engine named in the startup banner.

```
GET  /health     unauthenticated. 200 only while the sim is stepping.
                 Names the engine, both GPUs, the exchange mode, measured
                 ms/step, and the `saturated` / `silent` flags.
GET  /mood       the vector, the smoothed Hz it came from, the instantaneous
                 Hz, the active drive, sim time and sample age.
GET  /describe   the engine's own description plus the whole calibration.
POST /drive      {"olfactory_ORN": 0.05, ...}  0 is silence, ~0.05 a clear
                 stimulus, ~0.3 saturates the site itself. An unknown name is
                 a 400, never a silent no-op.
POST /reset      membrane state back to zero.
```

Shape notes, each of which is a trap avoided rather than a preference:

* **One thread touches the simulation.** CUDA contexts are thread-bound and
  `Connectome` is not thread-safe, so the sim thread owns it and HTTP handlers
  only read the last published snapshot or push a command onto a deque. A
  request can never stall the network's clock.
* **Smoothing is in simulation time, not wall time.** The sim runs at 0.22x
  realtime; a wall-clock EMA would silently change the mood's time constant
  whenever the step rate changed.
* **It does not fall back to the CPU.** 352 ms/step is 0.003x realtime, a mood
  vector minutes behind the stimulus that caused it. No CUDA, no T400, or a
  kernel that will not compile -> the service stays up, `/health` returns 503
  with the reason and `/mood` returns 503. A wrong mood is worse than a
  missing one, and `Restart=always` on a hard exit would hide the reason in a
  crash loop.
* **No calibration, no service.** `moodd.py` will not invent an axis range.
  A missing or invalid `mood_calibration.json` is reported through `/health`
  and serves 503s.
* **`env.sh` is in the unit as explicit `Environment=` lines.** cupy
  JIT-compiles every elementwise kernel through NVRTC, so it needs CUDA
  *headers* on `CUDA_PATH`, not just the runtime libraries the speech stack
  needs. The failure mode is misleading: the T400 opens and reports its name,
  and then the first kernel dies with "Failed to find CUDA headers". A device
  that opens is not evidence the stack works.

## 5. Acceptance evidence

`probe_moodd.py` runs against the live service and all 21 checks pass
(`measurements/probe.log`). Measured vector, each row the mean of six samples
after the EMA has settled:

| condition | arousal | novelty | valence | reinforcement | agitation |
|---|---|---|---|---|---|
| rest | 0.000 | 0.042 | -0.001 | 0.026 | 0.889 |
| rest again | 0.001 | 0.044 | 0.006 | 0.026 | 0.826 |
| `olfactory_ORN=0.05` | 0.394 | 0.704 | 0.595 | 0.599 | 0.847 |
| `olfactory_ORN=0.20` | 0.923 | 0.970 | 0.959 | 0.961 | 0.880 |
| `mechanosensory=0.20` | 0.933 | 0.507 | 0.422 | 0.460 | **0.233** |
| back to rest | - | - | - | - | drift 0.069 |

All five axes move, two different stimuli produce two different *shapes* of
vector, and nothing is pinned at an end stop. The tolerance for "came back to
rest" is not a chosen constant: the probe measures the service's own
rest-to-rest jitter first (worst axis 0.064, escape_GF, 34 neurons) and
requires the return to be within 3x of it.

Also verified live:

* `systemctl restart` -> healthy again in 6.0 s, step counter from 26,400 back
  to 600, `/mood` serving.
* Live `ms_step` 4.766, `realtime` 0.2098x, reported from the running loop.
  Note that this number was wrong at first and the mistake is worth keeping in
  mind: timing `sim.step()` alone measures kernel *launch* time, because cupy
  queues asynchronously - a 100-step chunk fits in the queue and the service
  cheerfully reported 0.86 ms/step and **1.16x realtime** while actually
  running at 0.21x. `rates_hz()` is the synchronisation point, so the timing
  window has to close after it.
* Degradation: started against a missing calibration file, the process stays
  up, `/health` returns 503 naming the file, `/mood` returns 503. No CPU
  fallback, no crash loop.
* Auth: `/mood` 401 without a token and with a wrong one, `/health` 200
  without one, unknown sensory population 400, out-of-range level 400.

## Who owns which card

| GPU | | |
|---|---|---|
| 0 | GTX 1660 SUPER, 6 GiB | `paperclip-sttd` (resident large-v3) + `paperclip-ttsd` (Kokoro), ~2.7 GiB. Also moondream via `ollama`. **Not ours.** |
| 1 | T400 4 GiB | `paperclip-moodd` shard, rows 0-53,047, ~200 MiB |
| 2 | T400 4 GiB | `paperclip-moodd` shard, rows 53,047-164,587, ~200 MiB |

`ollama.service` is fenced to `CUDA_VISIBLE_DEVICES=0` by a drop-in that names
this reservation, which is the right arrangement: the sim uses only ~200 MiB of
each 4 GiB T400, so a co-tenant *fits* and would still halve the step rate,
because this workload is bandwidth-bound and already saturates both cards at
98-99%. During this work a transient manual `ollama serve` with
`CUDA_VISIBLE_DEVICES=1,2` loaded ~2.4 GiB onto each T400 and stretched a
7-second calibration condition to ~30 s. Nothing broke, but a step-time number
measured next to a co-tenant is not a step-time number - check
`nvidia-smi --query-compute-apps` before quoting one.

## Out of scope here

No Home Assistant ingest (blocked on a token), no language model (blocked on a
user decision), no Discord or n8n wiring. The sensory sites are driven
synthetically through `POST /drive` until the real ingest exists; GPU 0's
whisper + Kokoro allocation is untouched.
