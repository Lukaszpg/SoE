# Project Diablo 2 Season 14 / SoE — Tooltip crash: static audit and native expansion specification

**Analysis date:** 2026-11-18  
**Source:** `ProjectDiablo-SoE-23IndexExclusions.dll (source binary supplied during investigation)` (uploaded binary)  
**SHA-256:** `85e20da4d680519381499997f3443194e01691a41202c0e2faf5347c3e1dbebb`  
**PE32 preferred image base:** `0x10000000`  
**Status:** Verified root-cause mechanism and relevant call sites; **no executable DLL patch has been produced**. Earlier unrelated D2Common crash explicitly excluded.

## Runtime evidence, as supplied by tester

On hovering a highly modified item, exception `C0000409` with fast-fail code `2` occurs during the stack security-cookie validation at `ProjectDiablo+0x3B754F` (`0x103B754F`). The cookie at `[EBP-4]` was overwritten by continuous UTF-16 item-tooltip text, including color codes. Before entering the formatter, `[EBP-4] XOR EBP` matched `[0x1046A00C]` (`8C38603B` on the test execution). A 4-byte hardware write breakpoint on the saved cookie stopped in D2Lang's `Unicode::strcat` copy loop; the reported next instruction was `D2Lang+... / 0x005BB006: mov cx,[edx]`, immediately after a `mov word ptr [esi],cx` writing to the protected address.

The caller's next instruction was at `ProjectDiablo+0x2B4ACF` (`0x102B4ACF`), directly after `call 0x10323830` at `0x102B4ACA`. **That call is within the original stat formatter, not the higher-level tooltip assembly.** The hardware breakpoint is direct runtime evidence of the overrun and identifies the append site.

## Confirmed native functions and contracts

### Formatter: `0x102B4750` (RVA `0x2B4750`)

This function accepts eight stack arguments, returns `ret 0x20`. Inspection establishes:

- `[EBP+0x10]`: UTF-16 output buffer pointer (loaded into ESI at `0x102B4776`)
- `[EBP+0x14]`: declared output capacity in UTF-16 code units (512 or 1024 at known direct call sites)
- Multiple calls to `0x10323830`, a lazy-resolved D2Lang `Unicode::strcat` entry point; **none of the outgoing-buffer append calls checks that passed capacity**.
- An assembly scan from function entry `0x102B4750` to its return at `0x102B4BBA` found **no read of `[EBP+0x14]`**. The numeric limit exists in the call interface but is currently ignored for output appending.

Output-buffer `Unicode::strcat` call sites (destination ECX=ESI):

`0x102B494D`, `0x102B4AAC`, `0x102B4AB7`, **`0x102B4ACA` (confirmed culprit)**, `0x102B4B72`, `0x102B4B7D`, `0x102B4B8A`, `0x102B4B9B`.

Separate `0x102B4A8F` uses a local destination and requires independent validation; do **not** blindly redirect every `call 0x10323830` in this function into a single guarded-output helper.

Intermediate local buffers: its `0x24DC` stack frame contains at least two initialized 0x200-byte scratch areas (256 UTF-16 code units each) and a separately initialized 0x1FF8-byte statistics area. Their independent append/format rules must be validated.

**Direct or forwarding call sites:**

| Call | Output capacity | Evidence |
|---|---:|---|
| `0x102B1885` | 512 UTF-16 code units | `push 0x200`; output `[ebp-0x488]` |
| `0x102B441C` | 1,024 | `push 0x400`; output `[ebp-0x808]` |
| `0x102F345D` | 1,024 | `push 0x400`; 0x800-byte initialized output at `[esp+0x940]` |
| `0x103A4D7D` | forwarded dynamically | wrapper pushes its caller's eight arguments to `0x102B4750` |

**Important:** the forwarding entry can have additional callers. The list of direct calls does not establish full coverage of every invocation.

### Parent: `0x102B43C0` (RVA `0x2B43C0`)

`0x102B43C3: sub esp,0x814` creates the fixed frame. The security cookie is at `[EBP-4]`. The output buffer begins at `[EBP-0x808]` and spans 1,024 UTF-16 code units. The buffer ends just below the security cookie, with four bytes between its end and the cookie.

At `0x102B440A` the parent pushes `0x400`, at `0x102B440F` loads `[EBP-0x808]`, and at `0x102B441C` calls the formatter. The formatter overruns the buffer for sufficiently verbose item descriptions. After the call, the parent uses unbounded Unicode string appends to assemble its result into **another destination passed in ECX**, which has no explicit capacity argument in the parent's ABI.

Parent's external-destination append call sites (all need the owning caller's capacity): `0x102B44C5`, `0x102B44D2`, `0x102B44DF`, `0x102B4515`, `0x102B4522`, `0x102B452F`, `0x102B454B`, `0x102B4558`, `0x102B4578`.

**Direct parent callers:**

| Call | Destination passed in ECX | Constraint |
|---|---|---|
| `0x102ADDEB` | `[EBP-0x80C]` local | ~1,024-code-unit local output; must qualify precisely |
| `0x102ADEC4` | external pointer in EBX | owning allocation not established in this audit |
| `0x102AFEAB` | `[EBP-0x604]` local | local buffer; verify precise size before changing producer output |
| `0x102B180A` | `[EBP-0x3E88]` local | neighboring stack-buffer offsets; no size contract currently passed |
| `0x102B3121` | `[ESP+0x6048]` local | outer UI description function allocates a large `0x8044` frame; still verify exact boundaries |

The parent ABI uses ECX (external destination), EDX (unit), and two stack args, with `ret 8`. A sound expansion will need an explicit capacity contract or another proven way of obtaining the **true** destination capacity at each caller. **Never assume every caller owns 4,096 characters.**

## Implementation specification

### Gate 1 — Make the existing formatter enforce its capacity

Implement an output builder in the original `0x102B4750` path, not a separate renderer. Track the actual `out`, `capacity`, and current length. Every append to the caller-provided output must check `current + incoming + 1 <= capacity` before any write. On insufficient capacity, report that the output was too small to the owning caller so that it can retry/grow; **do not silently discard modifiers or write partial UTF-16/color-code sequences**. Also validate intermediate 256-code-unit scratch buffers separately.

The eight output-append sites identified above must all be covered. The local append at `0x102B4A8F` has a different destination and should remain on its own capacity path.

### Gate 2 — Enlarge the actual `0x102B43C0` intermediate

Provisional target: 4,096 UTF-16 code units. Must update, as one atomic change:

- stack allocation or allocation strategy;
- initialized buffer start, length, and all EBP-relative references, including dynamic indexed reads/writes;
- local scratch-variable placements (`[EBP-0x814]`, `[EBP-0x810]`, `[EBP-0x80C]`);
- `0x400` passed to formatter and all 0x800 **byte** comparisons tied to the old buffer;
- frame security cookie and every epilogue path.

The original stack frame `0x814` is less than a page. Resizing it above one page requires **correct stack probing** (or a suitable owned allocation strategy), not only replacing `sub esp,0x814` with a larger constant. Do not overwrite other stack locals or bypass `/GS` checks.

### Gate 3 — Update every destination beyond the intermediate

The parent immediately appends its intermediate result into another buffer supplied by its caller. Introduce/qualify a capacity contract for that destination and update **all five direct callers**, plus any indirect/exported callers. Some call sites use only ~1,024-code-unit buffers. Set a destination's expanded size only after its actual allocation and downstream consumers have been expanded to match.

Follow the final tooltip into native `D2Client.dll` and PD2 `BH.dll`. These contain their own 1,024-character assumptions, which must not be bypassed merely by producing a longer intermediate string. The existing BH preparatory branch `feature/soe-tooltip-bh-capacity-prep` retains the 1,024-character native contract intentionally.

### Gate 4 — Validate before release

- Verify the exact DLL SHA and instruction bytes before every patch application; refuse unknown versions.
- Preserve the original item stat ordering, color codes, damage-group formatting, filtering and socket/set handling.
- Test crashing item and a boundary suite (original cap -1, cap, cap+1, near new cap) with hardware write watchpoints/guard values around every destination.
- Verify all formatter callers at both 512- and 1,024-character sizes; no caller may write beyond its true allocation.
- Verify ordinary/magic/rare/unique/set/socketed/runeword tooltips, BH stat ranges, ilvl and long names, alternate resolutions and renderer clipping.
- Remove temporary diagnostics, ensure no player-facing incomplete text, and back up all original DLLs.

## Decision

**Root cause qualified; full expansion patch not yet safe to produce.** A quick `0x400 -> 0x1000` or single `sub esp` change will silently corrupt the smaller caller buffers. An interim guard-only patch would prevent a crash by suppressing excess text, but it does **not** fulfill the requested feature of displaying every modifier and is not included here. The correct solution is coordinated expansion with capacity-aware construction in the existing native path.

## Reproducibility

Run `python verify_projectdiablo_tooltip.py <path-to-ProjectDiablo.dll>` using the separately prepared **read-only** script. It validates the SHA-256, PE base, and 33 instruction locations against the audited build. It never changes the DLL.