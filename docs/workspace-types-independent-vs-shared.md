# WORKSPACE TYPE: independent vs shared

Status: **ABANDONED — do not implement.**  
Decision: 2026-09-14 — do **not** add a WORKSPACE TYPE / WORKSPACE METHOD
panel section or settings. Keep current independent behavior only
(`ext-*` on Miracast, numerics on eDP, plus `pin_extend_workspaces`).

The sections below are retained only as historical research (Hyprland limits,
Persist reconnect pin incident). They are **not** a backlog.

Date: 2026-09-14  
Context: Extend Miracast on Hyprland (`persistent-miracast` head + eDP).

---

## 1. What we observed / fixed (independent type)

### Symptom
After many Persist reconnects (encode quality matrix), eDP and Miracast desktops were no longer separate:

| Workspace | Was on (broken) | Should be (independent) |
|-----------|-----------------|-------------------------|
| `ext-1` | **eDP-1** | `persistent-miracast` |
| `3` (numeric) | **persistent-miracast** | eDP-1 |
| `ext-2` | persistent-miracast | persistent-miracast |
| `1`, `2` | eDP-1 | eDP-1 |

Users saw “the same strip / mixed desktops” instead of laptop numerics vs TV `ext-*`.

### Root cause
With **Persist display** on, `ensure_extend_monitor` **skipped** `seed_extend_workspaces` whenever the cast head already had occupied workspaces. That path only moved **non-`ext-*` off the cast head**; it never pulled stranded `ext-*` **back onto** the cast head. Repeated matrix reconnects (`stop --keep-workspaces` + `start`) left `ext-1` on eDP permanently.

### Fix landed (independent behavior)
Commit `3c8963d` — `fix: re-pin ext-* to Miracast head on every Persist connect`:

- New `pin_extend_workspaces`:
  - All `ext-*` → cast monitor
  - All non-special non-`ext-*` on cast → primary (eDP)
- Persist + occupied cast head: still **pins**; only skips the “fresh seed / focus create ext-1” ceremony
- Empty-head path: `seed_extend_workspaces` calls pin, then focuses `ext-1` on cast

One-shot live repair during the incident also moved `ext-1` → Miracast and `3` → eDP.

### Independent model (current product behavior)
This is what Omarchy Miracast + `colin.workspaces` already implement:

| Display | Workspace identity | Switching |
|---------|-------------------|-----------|
| eDP (laptop) | Numeric `1`…`10` | SUPER+N on laptop focus |
| Miracast Extend | Named `ext-1`…`ext-10` | SUPER+N on cast focus (via `omarchy-hyprland-workspace-focus`) |

- **Two separate pools** of desktops (different workspace objects).
- Switching on one monitor does **not** change the other monitor’s active workspace.
- Windows live on exactly one workspace → one monitor at a time.
- Bar strip: laptop shows numeric slots; Miracast / HDMI show `ext-*` slots (`colin.workspaces`).

Related settings (orthogonal to WORKSPACE TYPE):

- **Persist display** (`preserveDisplayAcrossMonitors`): keep the same Extend *head* (`persistent-miracast`) and its `ext-*` desktops when switching sinks — not “share with eDP”.

---

## 2. Investigation: can one workspace show on two displays without mirroring?

### Short answer
**No — not with Hyprland’s native workspace model**, for the combination you described:

> same workspace on eDP and Miracast **and** independently switch workspaces per display **and** all workspaces shared.

Those three requirements are inconsistent under Hyprland (and typical tiling WMs).

### Hyprland facts (0.56.x / wiki)
1. A **workspace is a single container of windows**, assigned to **one monitor** at a time (`workspace_rule` → `monitor:`).
2. Each monitor has **one active workspace**.
3. Putting the **same pixels** on two outputs is **`monitor.mirror`** — true screen mirroring, not an independent second “view” of a workspace with its own switcher.
4. Maintainer guidance / discussions: you cannot bind one workspace identity to multiple monitors as a dual view ([Discussion #10088](https://github.com/hyprwm/Hyprland/discussions/10088) — “you can’t do that”).
5. Community “same number on both monitors” tools (`hyprsplit`, `split-monitor-workspaces`, `hypr-local-workspaces`) create **per-monitor clones of numbering** (e.g. “1” on A and “1” on B are **different** workspaces / namespaced IDs). Switching is independent **because they are not the same workspace object**.

### Mapping your hypothetical “shared” ask

| Requirement | Native Hyprland? | Notes |
|-------------|------------------|--------|
| Same **windows** visible on eDP and Miracast at once | Only via **mirror** | Not independent switching |
| Independently switch “desktop” per display | **Yes** (default) | Each monitor’s active WS is independent |
| Shared **pool** of workspaces (any WS can move to either display) | **Yes** | Move workspace to monitor; still only active on one |
| Same WS **active on both** monitors simultaneously | **No** | Would require dual-assignment or clone views |
| Shared **slot labels** (both show “1”…) with independent content | **Emulated only** | Two workspaces, paired naming — not one shared WS |

### Coherent interpretations of “shared” (if we still want a mode)

**A. Shared pool (Hyprland-native, recommended if we add a type)**  
- One global set of workspaces (`1`…`N` or named).  
- Each monitor shows one active WS.  
- Switching on eDP does not force Miracast to change (and vice versa).  
- A given workspace can be on only one monitor; moving it to Miracast removes it from eDP.  
- **Not** “same workspace on both screens.”

**B. Paired slots (KDE-like dual-desktop switch)**  
- SUPER+2 switches **both** monitors to their slot-2 workspace (`2` on eDP and `ext-2` / `2@miracast` together).  
- Looks “shared numbering”; still **two** workspace objects; optional “lock switch” vs independent.  
- Does **not** show the same windows on both displays unless you also mirror.

**C. Mirror**  
- Miracast output `mirror = eDP-1` (or reverse).  
- Identical image; no independent workspace strip on the TV.

**D. Plugin / compositor fork**  
- Hypothetical dual-viewport of one workspace — not available in stock Hyprland; out of scope unless we adopt an external project that implements it (none found that match “same WS, two views, independent switch”).

### Conclusion for product design
- **Independent** (current): separate pools (`1..10` vs `ext-1..10`) — already shipped; pin fix restores it after Persist reconnects.  
- **Shared** as “same workspace on both displays + independent switching” is **not feasible** on stock Hyprland without mirroring (which kills independence) or lying about “same” (paired distinct workspaces).  
- A honest **shared** mode should mean **A** (shared pool, one WS per monitor) and/or **B** (optional paired switching), documented clearly so users don’t expect a dual view of one desktop without mirror.

### Clarification: one global active workspace + focus vs read-only (2026-09-14)

Revised ask:

> There would only be **one active workspace** at a time. One display has **focus**; the other is **read-only**. Same content on eDP and Miracast, without calling it “screen mirroring” in the product sense.

**Does that change feasibility?** It removes the contradiction with per-display independent switching, but it does **not** unlock a non-mirror dual view in Hyprland.

| Piece | Feasible? | How |
|-------|-----------|-----|
| Single global active desktop (switching changes what *both* show) | **Yes** | Always show the same workspace identity on both outputs |
| Same windows / same layout on both screens | **Yes, via clone** | Hyprland `monitor.mirror`, or Miracast **Mirror** cast mode (capture eDP) |
| Focused display interactive, other read-only | **Mostly yes** | One seat: cursor/keyboard follow the focused monitor; keep focus on eDP (or cast) and don’t focus the read-only output. Optional: stronger “input lock” so clicks on the TV do nothing useful |
| Same result **without** compositor mirror / capture clone | **No** | Stock Hyprland still won’t assign one workspace to two monitors as two live viewports. No supported “secondary read-only camera” into a workspace on another output |

So the revised model is essentially:

**Shared-clone (focus + read-only)** ≈ **mirror (or Mirror cast)** + **input policy**  
not a third compositor primitive.

Implications for WORKSPACE TYPE naming:

- If product “shared” means “TV always shows what I’m looking at, I drive it from the laptop,” ship it as **clone/mirror semantics** (possibly wrapped as WORKSPACE TYPE `shared`) and be honest that the TV is a read-only view of the focused desktop.
- That path overlaps heavily with existing **Cast mode = Mirror**; Extend + `persistent-miracast` + `mirror=eDP` is the Hyprland-native way to get the same pixels on the headless output.
- Independent switching of a *different* desktop on the TV is then explicitly **out of scope** for `shared`.

Updated coherent modes:

| Mode | Active WS | What’s on TV | Input |
|------|-----------|--------------|--------|
| **independent** (now) | Two (numeric + `ext-*`) | Separate desktop | Per focused monitor |
| **shared-pool (A)** | Two (one per monitor) | Different WS from shared pool | Per focused monitor |
| **shared-clone (revised ask)** | **One** global | Same as focused desktop | Focus display RW; other RO |
| **paired (B)** | Two, switched together | Different content, same slot # | Per monitor or locked |

---

## 3. Plan: add WORKSPACE TYPE (`independent` | `shared`)

### Goals
- Panel + `miracast-ctl` setting: **WORKSPACE TYPE**.  
- Default: **`independent`** (current behavior + pin_extend_workspaces).  
- **`shared`**: pick one honest meaning after product choice:
  - **`shared-pool`**: interpretation **A**, or  
  - **`shared-clone`**: revised ask (one active WS, focus + read-only) via mirror / Mirror cast + focus policy.  
- Never freeze eDP; never `output remove` for workspace mode changes.

### Non-goals (v1)
- Dual interactive viewports of one workspace without clone/mirror.  
- Changing Persist-display semantics (keep as sink-head persistence) unless `shared-clone` replaces Extend with mirrored headless.

### Settings sketch
```json
{
  "workspaceType": "independent",
  "workspacePairedSwitch": false
}
```

| Value | Behavior |
|-------|----------|
| `independent` | eDP: numeric; Miracast: `ext-*`; pin on every connect; bar strips differ |
| `shared` (pool) | Both monitors use the same numeric pool; a WS lives on one monitor; per-monitor active WS |
| `shared` (clone) | One global active WS; TV mirrors focused desktop; input stays on focus display (read-only TV) |

### Implementation phases

#### Phase 0 — Spec lock (no code)
- [ ] Confirm with user: **shared = A** (shared pool), not dual-view.  
- [ ] Decide whether paired switch (**B**) is a sub-option or separate.  
- [ ] Document interaction with Persist, Mirror cast mode, and matrix reconnects.

#### Phase 1 — Independent hardening (mostly done)
- [x] `pin_extend_workspaces` on Persist connect  
- [ ] Call pin from matrix reconnect path explicitly (belt-and-suspenders)  
- [ ] Small test / script asserting ext-* ∉ eDP and numerics ∉ cast after `ensure_extend_monitor`  
- [ ] README / BUILD: name this mode **WORKSPACE TYPE = independent**

#### Phase 2 — Shared pool (A)
- [ ] On connect in `shared`: do **not** seed `ext-*`; ensure cast head takes next free numeric or last-used WS  
- [ ] Disable / no-op moves that force `ext-*` onto cast  
- [ ] `omarchy-hyprland-workspace-focus` + `colin.workspaces`: when focused monitor is Miracast, SUPER+N targets global numerics (same as eDP), not `ext-N`  
- [ ] Migrate existing `ext-*` windows onto numeric WS once when switching independent → shared (one-shot prompt)  
- [ ] Disconnect: optional migrate cast WS back to eDP vs leave on parked head (Persist)

#### Phase 3 — UX
- [ ] Panel: WORKSPACE TYPE pills next to Persist (or under CONTROLS)  
- [ ] `miracast-ctl set-workspace-type independent|shared`  
- [ ] Status JSON field `workspaceType`  
- [ ] Warn on Mirror cast mode: shared/independent both moot if output is mirrored

#### Phase 4 — Optional paired switch (B)
- [ ] `workspacePairedSwitch`: SUPER+N dispatches focus on **both** monitors to slot N (numeric on eDP, `ext-N` or paired ID on cast in independent; same ID on both only if shared pool allows — usually still two WS)  
- [ ] Explicitly document: paired ≠ same windows on both screens

### Test plan
1. **Independent regression**: connect → `ext-*` only on cast; numerics only on eDP; after 5× Persist reconnect, pin still holds.  
2. **Shared pool**: create WS 5 on eDP, move to Miracast, confirm eDP no longer shows 5; switch eDP to 1 without changing Miracast’s active WS.  
3. **eDP safety**: no `output remove`; cursor returns to eDP after connect.  
4. **Matrix**: reconnect loop does not strand workspaces (pin).  

### Open questions
1. Does product **shared** mean **shared-pool (A)** or **shared-clone** (one active WS, focus + read-only TV)?  
2. If clone: implement via Hyprland `mirror=` on `persistent-miracast`, or reuse Cast mode **Mirror** (capture eDP)?  
3. When switching independent → shared-pool, migrate `ext-*` automatically or ask?  
4. Bar indicator: one strip for shared-clone vs two strips for independent / shared-pool?  

---

## 4. References
- Hyprland Workspace Rules: https://wiki.hypr.land/Configuring/Basics/Workspace-Rules/  
- Hyprland Monitors (`mirror`): https://wiki.hypr.land/Configuring/Basics/Monitors/  
- “Bind workspace to multiple monitors”: https://github.com/hyprwm/Hyprland/discussions/10088  
- Forum: workspaces across two monitors (KDE-like): https://forum.hypr.land/t/workspaces-on-multiple-monitors/1371  
- Per-monitor numbering (not same WS): hyprsplit / split-monitor-workspaces / hypr-local-workspaces  
- Local: `pin_extend_workspaces` / `seed_extend_workspaces` in `bin/miracast-ctl`; `colin.workspaces` bar strip  

---

## 5. Incident notes (2026-09-14 matrix)
- Encode matrix reconnects exposed the Persist skip-seed hole.  
- Live repair: moved `ext-1` → `persistent-miracast`, `3` → `eDP-1`.  
- Matrix continued per user request; pin fix applies to subsequent connects.
