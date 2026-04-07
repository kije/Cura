# Reliability & Robustness Review

**Reviewer:** reliability-expert
**Date:** 2026-04-07
**Scope:** All Python files in `plugins/FEAInfillOptimizer/`
**Mandate:** The plugin must NEVER freeze, crash, or corrupt data.

---

## Executive Summary

The plugin demonstrates solid defensive patterns: progress throttling (500ms), `callLater` thread marshaling, cascade solver fallbacks (spsolve -> CG), mesh tetrahedralization cascade (pytetwild -> gmsh -> scipy), degenerate element masking, and orphan overlay cleanup. However, this review identifies **4 Critical**, **7 High**, **6 Medium**, and **4 Low** severity issues that could cause freezes, crashes, memory leaks, or silent data corruption under realistic conditions.

**Top 3 risks by impact:**
1. Signal multi-connect in `_recheckDeps()` — cumulative UI freeze from duplicated handlers
2. Zombie spsolve threads — unbounded resource leak on timeout
3. OOM in stiffness assembly — no memory guard on large meshes

---

## Issue Catalog

### CRIT-1: Signal Multi-Connect in `_recheckDeps()` (Critical)

**File:** `FEAInfillExtension.py` ~line 238
**Impact:** UI freeze, duplicate side-effects, cumulative performance degradation
**Mechanism:** Each call to `_recheckDeps()` re-connects `MachineManager.activeMaterialChanged`, `sceneChanged`, and other signals without first disconnecting. If dependency installation fails and retries, or if `_recheckDeps` is called from multiple paths, each signal fires N handler copies. The `_onFEAProgress` handler (500ms throttle) multiplied by N connections effectively reduces the throttle to 500ms/N, flooding the UI thread.

**Reproduction:** Call `_recheckDeps()` 5 times (e.g., by toggling dependency availability). Trigger `sceneChanged` — observe 5x handler invocations.

**Fix:** Disconnect signals before reconnecting, or guard with a `_signals_connected` flag:
```python
def _recheckDeps(self):
    self._disconnectAllSignals()  # new method
    # ... then reconnect
```

**Cross-ref:** CQ-8 from code-quality-expert.

---

### CRIT-2: No Cancel Propagation to Running Solver (Critical)

**File:** `FEAInfillExtension.py` `cancelAnalysis()`, `jobs/fea_solve_job.py`
**Impact:** User presses Cancel but solver continues for 30-120s consuming CPU
**Mechanism:** `cancelAnalysis()` sets `_cancel_requested = True` and calls `job.cancel()`, but `FEASolveJob.run()` only checks cancellation at coarse checkpoints (between major phases). The inner `fea_solver.solve()` has no cancellation check — a 30s spsolve timeout blocks the entire job thread. The iterative solver's per-iteration loop also lacks cancel checks.

**Reproduction:** Start analysis on a large mesh, click Cancel immediately. The job thread continues until the current solver phase completes (up to 120s for EasyFEA timeout).

**Fix:** Pass a `cancel_event: threading.Event` into `solve()` and `iterate()`. Check it:
- Before each spsolve/CG call
- At the start of each SIMP iteration
- Inside CG callback (`callback=check_cancel`)

---

### CRIT-3: Zombie spsolve Thread on Timeout (Critical)

**File:** `fea/fea_solver.py` ~`solve()` method
**Impact:** Unbounded resource leak; potential memory corruption
**Mechanism:** When the 30s spsolve timeout fires, the solver falls back to CG. But the `daemon=True` spsolve thread continues running `scipy.sparse.linalg.spsolve` to completion. For a large system, this may take minutes and consume GBs of RAM. Multiple cancelled analyses create multiple zombie threads. Since scipy's UMFPACK/SuperLU is not thread-safe, concurrent zombie threads operating on the same matrix data could corrupt memory.

The same zombie pattern exists in the EasyFEA path: `_solve_worker` thread (`iterative_solver.py` ~line 405) with a 120s timeout. If the EasyFEA solve times out, that thread also continues running.

**Severity escalation (from test review):** In OC/SIMP optimization with 20 iterations, each iteration calls `solve()`. If spsolve times out on a nearly-singular system, every iteration spawns a zombie thread. A 20-iteration OC solve could create **20 concurrent zombie spsolve threads**, each holding the full stiffness matrix in memory. Additionally, the 120s EasyFEA timeout over 20 iterations means a global worst-case of **40 minutes** of blocking with no global timeout.

**Reentrancy/correctness concern (from physics review):** If the user triggers a second analysis while a zombie spsolve thread is still running, both threads may simultaneously access scipy/LAPACK internals. Some LAPACK implementations (especially OpenBLAS) are not reentrant — concurrent calls can produce wrong numerical results or segfault.

**Reproduction:** Run analysis on a 500K+ element mesh. Let spsolve timeout, triggering CG fallback. The spsolve thread continues in background (observe via `threading.enumerate()`). Start a second analysis before the zombie finishes.

**Fix:** spsolve cannot be interrupted from Python. Options:
1. Run spsolve in a `subprocess` (killable) instead of a thread
2. Use `scipy.sparse.linalg.splu` (factorize) + forward/back-solve (interruptible at factor stage)
3. Accept the zombie but add a process-level memory watchdog that kills the thread's subprocess
4. At minimum: guard against concurrent analyses by refusing to start a new solve while a zombie thread from a previous solve is still alive (check `threading.enumerate()` for active solver threads)
5. Add a global solve timeout for the entire OC iteration loop (e.g., 10 minutes) in addition to per-solve timeouts
6. Track zombie thread count per analysis; abort OC iteration if zombie count exceeds threshold (e.g., 3)

---

### CRIT-4: OOM Risk in Vectorized Stiffness Assembly (Critical)

**File:** `fea/fea_solver.py` — stiffness assembly
**Impact:** Cura crash (Python OOM kill)
**Mechanism:** The vectorized assembly allocates `(M, 12, 12)` float64 arrays where M = number of elements. At 500K elements: `500000 * 12 * 12 * 8 = 5.5 GB`. Combined with the duplicate COO arrays (data, row, col), peak memory can exceed 16 GB, triggering OS OOM kill on machines with 16 GB RAM.

**Reproduction:** Import a high-poly mesh (500K+ faces). The tetrahedralization may produce 1M+ tets. Run analysis.

**Fix:**
1. Add element count guard: warn and refuse above a configurable threshold (e.g., 200K elements)
2. Use chunked assembly: process elements in batches of 50K, accumulate into COO arrays incrementally
3. Report estimated memory before assembly so user can abort

**Cross-ref:** Performance-expert OOM finding.

---

### HIGH-1: `id()` Reuse Risk in Overlay Parent Tracking (High)

**File:** `visualization/stress_overlay.py` — `_fea_parent_node_id = id(node)`
**Impact:** Overlay attached to wrong model after node deletion
**Mechanism:** `id(node)` returns a CPython memory address. If a CuraSceneNode is deleted and a new one allocated at the same address, `_fea_parent_node_id` matches the wrong node. The orphan cleanup in `cleanup_orphaned_overlays()` would then skip a truly orphaned overlay (it "finds" a parent) or, worse, associate it with an unrelated model.

**Reproduction:** Delete a model with an overlay. Import a new model. If CPython reuses the address, the overlay persists attached to the new model.

**Fix:** Use `node.getId()` (Cura's stable unique ID) or store a `weakref.ref(node)` with a destroy callback.

---

### HIGH-2: `id()` Reuse Risk in Node Cache (High)

**File:** `FEAInfillExtension.py` — `_node_cache` keyed by `str(id(node))`
**Impact:** Stale cache data applied to wrong model
**Mechanism:** Same `id()` reuse issue as HIGH-1. If the WeakValueDictionary evicts a dead node but a new node gets the same `id()`, cache lookups return stale data for the new node.

**Fix:** Key by `node.getId()` instead of `str(id(node))`.

---

### HIGH-3: gmsh Initialization Race Condition (High)

**File:** `fea/tetrahedralization.py` — pytetwild path
**Impact:** gmsh crash or corrupted mesh output
**Mechanism:** The pytetwild fallback path calls `gmsh.initialize()` without acquiring `_GMSH_LOCK`. If two parallel analyses trigger tetrahedralization simultaneously (one using gmsh path, one using pytetwild path that also calls gmsh), the gmsh library state corrupts — gmsh is not thread-safe.

**Reproduction:** Start two analyses on different models simultaneously. If one takes the pytetwild path and calls `gmsh.initialize()` while the other is mid-gmsh meshing, crash or garbled output results.

**Fix:** All gmsh API calls (including `gmsh.initialize()` in the pytetwild path) must be wrapped in `_GMSH_LOCK`.

---

### HIGH-4: Direct BC Data Mutation Without Undo (High)

**File:** `BoundaryConditionTool.py` — `setUpdateForceAtIndex()`
**Impact:** Irreversible data corruption — no way to undo force vector changes
**Mechanism:** `setUpdateForceAtIndex()` directly mutates `fg.force[index] = new_value` without creating an `UndoOperation`. All other BC mutations (add/remove/clear) use the undo stack, but individual force component edits bypass it. If the user accidentally sets a force to an extreme value, they cannot undo it.

**Reproduction:** Edit a force component in the UI. Press Ctrl+Z. The force change is not reverted.

**Fix:** Create `UpdateForceOperation` similar to existing BC operations. Wrap the mutation in an undoable operation.

---

### HIGH-5: EasyFEA Observer Leak on Exception (High)

**File:** `fea/iterative_solver.py` — EasyFEA path
**Impact:** Memory leak, incorrect FEA results from stale observers
**Mechanism:** The observer is added via `problem.add_observer(...)` early in the iteration. If an exception occurs between observer registration and the `finally` block that calls `_Remove_observer`, and if `_Remove_observer` itself throws (e.g., observer already removed by EasyFEA internally), the observer persists. On subsequent solves reusing the problem object, stale observers fire.

**Mitigation:** The current `try/except` around `_Remove_observer` is good but silently swallows exceptions. Log the failure.

**Fix:** Use a context manager pattern:
```python
@contextmanager
def _easyfea_observer(problem, callback):
    problem.add_observer(callback)
    try:
        yield
    finally:
        try:
            problem._Remove_observer(callback)
        except Exception as e:
            Logger.log("w", f"Observer cleanup failed: {e}")
```

---

### HIGH-6: Unthrottled `_onSceneNodeMayHaveBCMetadata` (High)

**File:** `FEAInfillExtension.py`
**Impact:** UI stutter on project load with many nodes
**Mechanism:** This handler fires on `sceneChanged` without throttling. When loading a 3MF project with dozens of models, each node addition triggers a full scan of all nodes for BC metadata. This is O(N^2) in node count during load.

**Reproduction:** Load a 3MF project with 20+ models that have BC metadata.

**Fix:** Throttle with the same pattern used for `_cleanupOrphanedOverlays` (1.0s debounce), or use a single-shot `QTimer` that batches all pending metadata syncs.

**Cross-ref:** Performance-expert finding.

---

### HIGH-7: Unbounded Daemon Threads for Hover (High)

**File:** `BoundaryConditionTool.py` — `_debounced_hover_compute`
**Impact:** Thread pool exhaustion, potential GIL contention causing UI stutter
**Mechanism:** Each mouse move over a mesh spawns a new daemon thread for BFS face-group computation. Fast mouse movement can spawn dozens of threads per second. While the `_hover_generation` counter invalidates stale results, the threads still run to completion before checking. On complex meshes, BFS can take 100ms+, creating dozens of concurrent threads fighting for the GIL.

**Mitigation:** The existing `_hover_in_progress` guard prevents re-entrant hover processing, but does not cap total thread count.

**Fix:** Use a single persistent worker thread with a queue, or `concurrent.futures.ThreadPoolExecutor(max_workers=1)` that discards stale work.

---

### MED-1: Module-Level Global `_last_containment_method` (Medium)

**File:** `fea/tetrahedralization.py`
**Impact:** Race condition in concurrent tetrahedralization
**Mechanism:** `_last_containment_method` is a module-level string that records which containment check succeeded. If two tetrahedralization jobs run concurrently, they overwrite each other's value. This affects diagnostic logging only (no functional impact), but violates thread safety principles.

**Fix:** Return the containment method as part of the result tuple instead of using a global.

---

### MED-2: No Input Validation on QML-Facing Property Setters (Medium)

**File:** `BoundaryConditionTool.py` — various `@pyqtSlot` methods
**Impact:** Invalid state from malformed QML input
**Mechanism:** Property setters called from QML (e.g., force magnitude, direction, material parameters) do not validate input ranges. A NaN or infinity from a QML spinbox bug would propagate into the FEA solver, producing garbage results or FPE crashes.

**Reproduction:** QML bug or manual testing passes `float('inf')` as force magnitude.

**Fix:** Add `math.isfinite()` checks on all numeric QML inputs. Reject with a warning message.

---

### MED-3: Advanced Settings UI/Backend State Divergence (Medium)

**File:** `BoundaryConditionPanel.qml` ~lines 1786-1833
**Impact:** Silent parameter mismatch — UI shows defaults but backend uses different values
**Mechanism:** Advanced Settings spinboxes (MinDensity, MaxDensity, NumZones, MaxIterations, BondingCoeff, VolumeFraction) use hardcoded `value:` defaults instead of reading from `toolProperties.getValue(...)`. If parameters are saved/restored, the UI won't reflect saved state.

**Cross-ref:** UX-10 from ui-ux-expert.

**Fix:** Bind spinbox values to `toolProperties.getValue("propertyName")` with appropriate defaults.

---

### MED-4: Broad Silent Exception Swallowing (Medium)

**Files:** Multiple locations across the plugin
**Impact:** Bugs hidden, making debugging extremely difficult
**Mechanism:** Many `try/except Exception` blocks log a warning and continue silently. While this prevents crashes (good for reliability), it can mask logic errors that produce subtly incorrect FEA results. The user has no indication that something went wrong.

**Examples:**
- `_onFEAProgress` signal emission failure
- `_cleanupOrphanedOverlays` exception during cleanup
- Observer removal in iterative solver

**Fix:** Distinguish between expected exceptions (log at debug) and unexpected ones (log at warning + set a "degraded" flag visible in UI).

---

### MED-5: Temp .msh File Lifecycle Gaps (Medium)

**File:** `jobs/fea_solve_job.py`
**Impact:** Disk space leak on repeated analysis
**Mechanism:** Temp .msh files are created in the system temp directory and cleaned up in the normal path. But if the process crashes mid-analysis (OOM, segfault from native code), temp files persist. Over many sessions, these accumulate.

**Fix:** Use `tempfile.NamedTemporaryFile(delete=True)` or add cleanup on plugin startup that removes stale `fea_*.msh` files older than 1 hour.

---

### MED-6: 500K Interior Point Cap in scipy Fallback May Still OOM (Medium)

**File:** `fea/tetrahedralization.py` — scipy Delaunay fallback
**Impact:** OOM crash on borderline meshes
**Mechanism:** The 500K interior point cap prevents the worst case, but scipy's Delaunay on 500K points still requires ~4 GB of RAM for the triangulation. On machines with 8 GB total, this can cause OOM.

**Fix:** Make the cap configurable and document memory requirements. Consider a lower default (200K) with UI warning.

---

### LOW-1: `_ensure_adjacency_async` Thread Not Joined on Tool Deactivation (Low)

**File:** `BoundaryConditionTool.py`
**Impact:** Minor resource leak
**Mechanism:** When the BC tool is deactivated, the adjacency-building background thread may still be running. It's a daemon thread so it won't prevent shutdown, but it holds references to mesh data.

**Fix:** Set a cancellation flag and join with timeout on tool deactivation.

---

### LOW-2: WeakValueDictionary Race in Node Cache (Low)

**File:** `FEAInfillExtension.py`
**Impact:** Rare KeyError on concurrent access
**Mechanism:** `WeakValueDictionary` can raise `KeyError` if a value is garbage-collected between the `in` check and the `[]` access. This is a known Python race condition with weak references.

**Fix:** Use `cache.get(key)` pattern instead of `key in cache; cache[key]`.

---

### LOW-3: No Graceful Handling of Corrupt 3MF BC Metadata (Low)

**File:** `FEABoundaryConditionDecorator.py` — `fromDict()`
**Impact:** Plugin crash on loading corrupt project file
**Mechanism:** `fromDict()` assumes the dictionary structure matches expectations. A corrupt or hand-edited 3MF file could provide malformed BC data (missing keys, wrong types), causing `KeyError` or `TypeError`.

**Fix:** Wrap `fromDict()` in validation with fallback to empty BC state:
```python
try:
    decorator.fromDict(data)
except (KeyError, TypeError, ValueError) as e:
    Logger.log("w", f"Corrupt BC data, resetting: {e}")
    # leave decorator in default empty state
```

---

### LOW-4: Missing `__del__` or Explicit Cleanup in Extension (Low)

**File:** `FEAInfillExtension.py`
**Impact:** Signal connections persist after plugin disable
**Mechanism:** If the plugin is disabled (not just Cura shutdown), signal connections remain active. Handlers fire on an extension in an inconsistent state.

**Fix:** Implement a `shutdown()` or `pluginDisabled()` method that disconnects all signals.

---

## Thread Safety Summary

| Resource | Protection | Issue |
|----------|-----------|-------|
| gmsh library | `_GMSH_LOCK` (threading.Lock) | pytetwild path skips the lock (HIGH-3) |
| `_last_containment_method` | None | Module-level global (MED-1) |
| `_hover_generation` counter | Monotonic increment + check | Sound (no issue) |
| `_hover_in_progress` flag | Boolean guard | Sound but doesn't cap thread count (HIGH-7) |
| `_cancel_requested` | Simple boolean (no lock) | Adequate for single-writer pattern |
| `_node_cache` (WeakValueDictionary) | GIL-protected | Weak ref race possible (LOW-2) |
| Progress signal emission | `_last_progress_time` throttle | Sound (500ms throttle) |
| Overlay cleanup | `_last_cleanup_time` throttle | Sound (1.0s throttle) |
| `callLater` marshaling | Qt event loop | Sound — correct pattern for thread-to-main |

---

## Crash Prevention Assessment

| Crash Vector | Current Protection | Adequacy |
|-------------|-------------------|----------|
| Degenerate elements | `valid` mask zeroes out bad elements | Good |
| Non-finite displacements | `RuntimeError` raised | Good |
| Singular stiffness matrix | spsolve timeout + CG fallback | Good but zombie thread (CRIT-3) |
| LAPACK reentrancy | None | **Inadequate** — zombie threads + new analysis = concurrent LAPACK (CRIT-3) |
| OOM from large mesh | None | **Inadequate** (CRIT-4) |
| gmsh segfault | Cascade fallback | Good |
| pytetwild crash | try/except with fallback | Good |
| Division by zero in stress | `np.maximum(volumes, 1e-30)` | Good |
| Corrupt 3MF BC data | No validation | **Inadequate** (LOW-3) |
| Infinite BFS loop | `MAX_BFS_ITERATIONS = 50000` cap | Good |
| Hover face count explosion | 500 face cap in highlight | Good |

---

## Data Corruption Prevention Assessment

| Corruption Vector | Current Protection | Adequacy |
|-------------------|-------------------|----------|
| BC undo/redo | Operation stack for add/remove/clear | Good except force edits (HIGH-4) |
| 3MF persistence | `toDict()`/`fromDict()` serialization | Good but no load validation (LOW-3) |
| Overlay orphans | `cleanup_orphaned_overlays()` | Good but `id()` reuse risk (HIGH-1) |
| Concurrent BC edits | Single UI thread for QML | Good (Qt guarantees) |
| Model transform sync | Overlays re-parent on transform | Good |
| Node copy (`__deepcopy__`) | Custom implementation in decorator | Good |

---

## Graceful Degradation Assessment

| Failure Mode | Degradation Behavior | Quality |
|-------------|---------------------|---------|
| Missing dependencies | UI prompts install, features disabled | Excellent |
| Solver failure | Error message displayed, model untouched | Good |
| Mesh extraction failure | Warning logged, analysis aborted | Good |
| Tetrahedralization failure | Three-level cascade fallback | Excellent |
| Material unknown | Falls back to PLA with warning | Good |
| No BCs defined | Error message, refuses to solve | Good |
| Cancel requested | Sets flag, checked at coarse points | **Inadequate** (CRIT-2) |

---

## Recommendations by Priority

### Immediate (Pre-Release Blockers)

1. **CRIT-1:** Fix signal multi-connect — add `_disconnectAllSignals()` before `_recheckDeps()` reconnects
2. **CRIT-2:** Thread cancel propagation — pass `threading.Event` into solver loops
3. **CRIT-3:** Address zombie spsolve — run in subprocess or add memory watchdog
4. **CRIT-4:** Add element count guard — refuse analysis above threshold, report estimated memory

### High Priority (Next Sprint)

5. **HIGH-1, HIGH-2:** Replace `id(node)` with `node.getId()` everywhere
6. **HIGH-3:** Acquire `_GMSH_LOCK` for all gmsh API calls including pytetwild path
7. **HIGH-4:** Create `UpdateForceOperation` for undoable force edits
8. **HIGH-5:** Use context manager for EasyFEA observers
9. **HIGH-6:** Throttle `_onSceneNodeMayHaveBCMetadata`
10. **HIGH-7:** Replace unbounded hover threads with `ThreadPoolExecutor(max_workers=1)`

### Medium Priority (Hardening)

11. **MED-2:** Input validation on all QML property setters
12. **MED-3:** Bind Advanced Settings spinboxes to backend state
13. **MED-4:** Differentiate expected vs unexpected exceptions
14. **MED-5:** Auto-cleanup stale temp files on startup
15. **MED-6:** Lower scipy interior point cap, document memory needs

### Low Priority (Polish)

16. **LOW-1:** Join adjacency thread on tool deactivation
17. **LOW-2:** Use `.get()` pattern for WeakValueDictionary access
18. **LOW-3:** Validate BC metadata on 3MF load
19. **LOW-4:** Implement plugin shutdown signal disconnection

---

## Cross-Discipline Findings

| Finding | Domain | Forwarded To |
|---------|--------|-------------|
| CRIT-1 signal multi-connect | Code Quality | code-quality-expert (CQ-8) |
| CRIT-2 cancel propagation | Code Quality | code-quality-expert (CQ-18) |
| CRIT-3 zombie spsolve LAPACK reentrancy | Physics/Solver | physics-math-expert (confirmed) |
| CRIT-4 OOM assembly | Performance | performance-expert |
| HIGH-3 gmsh race | Physics/Solver | physics-math-expert (confirmed) |
| HIGH-4 undo gap | UI/UX | ui-ux-expert |
| HIGH-6 unthrottled handler | Performance | performance-expert |
| MED-2 QML validation | UI/UX | ui-ux-expert |
| MED-3 spinbox state | UI/UX | ui-ux-expert (UX-10) |

---

## Cross-Discipline Findings Received

| Source | Finding | Impact on Reliability |
|--------|---------|----------------------|
| physics-math-expert | **EasyFEA `Gl` bug**: `iterative_solver.py:304` uses `Gl=G_base` instead of `Gl=bonding_coeff * G_base`. Transverse shear overestimated by 1/k in EasyFEA path. | Moderate — produces incorrect FEA results in EasyFEA path, but OC/SIMP optimization forces `use_easyfea=False` so optimization is unaffected. Heuristic-only path affected. |
| physics-math-expert | Zombie spsolve LAPACK reentrancy — OpenBLAS not reentrant, concurrent zombie + new analysis = segfault or wrong results. | Elevates CRIT-3 severity — not just a resource leak but a correctness/crash vector. |
| physics-math-expert | EasyFEA `_solve_worker` thread (~line 405) has same zombie pattern with 120s timeout. | Extends CRIT-3 scope to cover both solver paths. |
| physics-math-expert | Verified correct: E_min 1% floor, CG fallback, degenerate detection, volumetric locking guard. | Positive — these defensive measures are sound. |
| ui-ux-expert | UX-10: Advanced Settings spinboxes use hardcoded defaults, not backend state. | Documented as MED-3. |
| ui-ux-expert | UX-29: `parseFloat(text) \|\| fallback` silently substitutes 100 N for "0" or empty input — no visual feedback. | Complements MED-2: backend `isfinite()` guards + QML visual rejection are two layers of same defense. |
| code-quality-expert | CQ-8 signal multi-connect, CQ-18 no cancel propagation, CQ-11 upgraded to Medium (MED-4), CQ-32 added (LOW-4). | Documented as CRIT-1, CRIT-2, MED-4, LOW-4. |
| performance-expert | OOM assembly, unthrottled metadata handler, zombie spsolve. | Documented as CRIT-4, HIGH-6, CRIT-3. |
| test-expert | 20-iteration OC can spawn 20 zombie spsolve threads; 120s x 20 = 40 min worst-case; CG fallback path has zero test coverage. | Escalates CRIT-3 severity; adds fix recommendations 5-6. |

---

## Conclusion

The plugin has a well-architected defensive structure — cascade fallbacks, throttled signals, `callLater` marshaling, and operation-based undo demonstrate strong reliability awareness. The four Critical issues (signal multi-connect, cancel propagation, zombie threads, OOM risk) are the only realistic paths to freeze or crash. The zombie thread issue is more severe than initially assessed: physics review confirmed that LAPACK reentrancy violations from concurrent zombie + new analysis threads can cause segfaults or silently wrong numerical results. Fixing these four issues would make the plugin safe for production use. The High-priority items address data integrity and resource management concerns that, while unlikely to cause catastrophic failure, could produce confusing behavior or gradual resource degradation over extended sessions.
