# PD2 Season 14 / SoE — Native item-tooltip capacity investigation

**Status (2026-11-18):** Root-cause write **confirmed by x32dbg hardware breakpoint**; five direct parent caller destinations **statically qualified**; per-stat scratch/BH interfaces and runtime-patched D2Client boundaries **not fully qualified**; native binary **not modified**. This is an investigation and implementation reference, **not a deployable patch**.

The earlier unrelated D2Common failure is deliberately **out of scope**.

## 1. Binary identity and address conventions

The evidence and instruction bytes refer to these exact uploaded binaries:

| DLL | SHA-256 |
|---|---|
| ProjectDiablo-SoE-23IndexExclusions.dll | `85e20da4d680519381499997f3443194e01691a41202c0e2faf5347c3e1dbebb` |
| D2Client.dll | `dd8bc6025de921216a97c17f97cd1a50fbb85926e838ec60e13451448836d906` |
| D2Common.dll | `6c350a82e0469519d51b21988e78134758dfa2aed58bd83674b49bfa6f786954` |
| BH.dll (original upload) | `4173b0c2146c0001ebecfb8b10be73915daf3b933f1fd6e05b71f11992692998` |

**ProjectDiablo** preferred image base: `0x10000000`; thus `0x102B4ACA` is VA, `+0x2B4ACA` is RVA. **D2Client** preferred base: `0x6FAB0000`; `D2Client+0x9426F` is RVA. Re-resolve addresses and check original bytes in other builds or when modules are relocated. Distinguish hexadecimal **B** from **8**: some initial screenshot readings and associated addresses were wrong. Use the verified binary manifests below rather than transcribed screenshots.

## 2. Root cause — confirmed runtime failure

With a heavily modified item hovered:

1. Windows reports `C0000409`, fast-fail subcode `2`: **stack security-cookie check failure**, not an arbitrary D2Common halt.
2. The ProjectDiablo security-cookie verifier is at **VA `0x103B754F`** (failure branch `0x103B7558`). Do not confuse it with unrelated code at `0x1038754F`.
3. Before the formatter, `dword(ebp-4) ^ ebp` matched `dword(1046A00C)` (both `8C38603B` in the debugger run). The cookie was initially valid.
4. A four-byte **hardware write breakpoint** on the parent function's saved cookie caught the write inside D2Lang `Unicode::strcat`. x32dbg stopped at `0x005BB006: mov cx,[edx]`, immediately after the preceding `mov word ptr [esi],cx` had written to the watched cookie (`ESI=001976C4` in that run).
5. The return address `0x102B4ACF` points just after **`0x102B4ACA: call 0x10323830`**, one of the output append calls in ProjectDiablo's stat formatter.
6. Memory around the overwritten cookie contained contiguous UTF-16 item-modifier text with D2 color codes. At `001976C0`: `006300FF` (`ÿc`); **`001976C4`: `0048003A`** (`:H`); `001976C8`: `00740061` (`at`), continuing a formatted item tooltip. The cookie address shown is **specific to that debugger run**, not a stable runtime address.

**Conclusion:** An item-tooltip string append in ProjectDiablo demonstrably writes past the current caller's buffer into its stack cookie. This is **not** yet proof that every oversized tooltip will fail only there, nor proof that D2Client's independent limits caused this specific crash.

## 3. Verified ProjectDiablo formatter and parent

| Site | Purpose / result |
|---|---|
| `0x102B43C0` | Parent function; fixed stack frame `0x814` bytes; saved cookie at `[EBP-4]` |
| `0x102B440A` | Pushes capacity `0x400` (1,024 UTF-16 code units) |
| `0x102B440F` | Passes local buffer starting at `[EBP-0x808]`, 2,048 bytes |
| `0x102B441C` | Calls formatter `0x102B4750` |
| `0x102B4750` | Formatter; output `[EBP+0x10]`, requested capacity `[EBP+0x14]` |
| **`0x102B4ACA`** | **Confirmed overflowing output append** through `Unicode::strcat` |
| `0x103A4D60` | Eight-argument forwarding entry into formatter; can also be called indirectly |

The formatter returns with `ret 0x20` and **does not read the capacity parameter** in the inspected body. Its eight caller-output append calls: `0x102B494D`, `0x102B4AAC`, `0x102B4AB7`, **`0x102B4ACA`**, `0x102B4B72`, `0x102B4B7D`, `0x102B4B8A`, `0x102B4B9B`. The append at `0x102B4A8F` targets **local scratch**, not the caller output; it must have a separate capacity check.

The parent then appends into a *different* caller-owned destination passed via ECX. Nine parent append sites are recorded in [projectdiablo-static-audit.md](projectdiablo-static-audit.md) and the [patch-site manifest](projectdiablo-patch-sites.csv). This downstream destination has **no capacity argument** in the original parent ABI.

## 4. Direct parent caller allocations — statically qualified

| CALL to parent (VA) | Destination capacity (UTF-16 units) | Allocation |
|---|---:|---|
| `0x102ADDEB` | **1,024** | Local `[EBP-0x80C]` |
| `0x102ADEC4` | **512 or 1,024**, for traced owners | External pointer in EBX, through two resolved owning paths |
| `0x102AFEAB` | **256** | Local `[EBP-0x604]` with adjacent buffer boundary |
| `0x102B180A` | **1,024** | Local `[EBP-0x3E88]` |
| `0x102B3121` | **4,096** | Local `[ESP+0x6048]` |

See [parent-caller-capacities.csv](parent-caller-capacities.csv) and [qualification-phase2.md](qualification-phase2.md) for exact initialization instructions, indirect ownership through `0x102ADF20`, and qualifications. These are sizes for **known direct paths**, not certification that no indirect calls exist.

**Impact:** Expanding the producer to 4,096 characters without growing or bounding its **256/512/1,024**-character recipients is unsafe.

## 5. Scratch buffers, callbacks, and runtime patch boundaries

- Formatter's local frame: `0x24DC` bytes, created through a stack-probing helper. It contains **two 256-character UTF-16 scratch areas** (each `0x200` bytes), a separate `0xA8`-byte area, and a `0x1FF8`-byte stat-pair area. Per-stat writes and all BH additions need individual bounds checking.
- Known formatter calls with explicit requested capacities: `0x102B1885` (512), `0x102B441C` (1,024), `0x102F345D` (1,024), plus forwarding from `0x103A4D60`.
- `0x103A4D60` is used in three PD2 callback patch records at `0x104A7170`, `0x104A7184`, `0x104A7198`. Their original D2Client targets are **CALL displacement operand RVAs** `+0x55F9C`, `+0x56038`, `+0x56098`, while the corresponding **CALL opcode RVAs** are `+0x55F9B`, `+0x56037`, `+0x56097`.
- PD2 dynamically loads BH with `GetProcAddress`: `0x102C49A0` → `_BHOnDamagePropertyBuild@16`; `0x102C49E0` → `_BHOnPropertyBuild@16`; `0x102C4970` → `_BHOnProperties@4`. None accepts an explicit destination capacity. Distinguish **per-stat** scratch from **whole-tooltip** buffers.
- The original uploaded D2Client binary predates the runtime-applied PD2 patches. **Inspect the in-memory hooks** before claiming the final consumer capacities are qualified.

## 6. Separate native D2Client constraints

Static inspection of the supplied *unpatched on-disk* `D2Client.dll` shows:

| D2Client RVA | Finding |
|---|---|
| `+0x560B0` → `+0x55B20` | 1,024-character formatter intermediate; output capacity is passed but ignored in inspected body |
| `+0x93450` | Main description and final popup each **1,024** characters; magic-modifier buffer **4,096** |
| `+0x9426F` | Unbounded append to the main 1,024-character destination |
| `+0x94431` | Unbounded copy into 1,024-character popup |
| `+0x94509` | 1,023-character clamp **after assembly**, too late to prevent earlier overwrites |

These are additional **static hazards**, not the proven trigger of the ProjectDiablo cookie failure. See [d2client-static-audit.md](d2client-static-audit.md).

## 7. BH PR #1 state

The current PR only prepares BH safely:

- `BH/Constants.h`: `ITEM_TOOLTIP_BUFFER_CAPACITY=1024`, `ITEM_PROPERTY_BUFFER_CAPACITY=1024`, `ITEM_TEXT_SIZE_LIMIT=1023`, `MAX_ITEM_TEXT_SIZE=512`.
- `BH/Modules/Item/Item.cpp`: bounds-aware all-or-nothing appends for BH's added description, ilvl/alvl, and damage-property text.
- BH capacity constants must **not** be increased before the corresponding caller-owned D2Client/ProjectDiablo buffers and the actual per-property scratch contract have been expanded.
- This branch has **not** been verified with an MSVC build or with an in-game regression test. PR remains **draft**.

## 8. Implementation specification and unresolved gates

**Keep the native tooltip construction path. Do not replace the renderer, bypass the /GS cookie, introduce substitute interception, or silently hide excess modifiers.**

1. **Scratch/BH qualification (remaining):** Map every write and maximum expansion of the 256-character scratch buffers and BH per-stat callbacks, including range annotations and color codes. Establish their actual pointer capacities.
2. **Patched D2Client call-boundary qualification (remaining):** Inspect the three in-memory CALL sites after PD2 hook installation and follow full parameter/return flow to every receiving allocation.
3. **Capacity-aware existing formatter:** Make *all eight* caller-output appends honor the passed capacity; treat the local scratch append separately. Propagate insufficient-capacity results to owner; do not truncate individual modifier lines or color escape sequences.
4. **Coordinated buffer expansion:** Provisional formatter intermediate **4,096** UTF-16 units, and final assembly **8,192** where justified. Update frame layout, locals, stack probing, buffer initialization, references, caller sizes, and native/BH consumers **atomically** and with exact build-byte validation.
5. **Runtime validation:** Reproduce original crashing item; test N−1/N/N+1 and near-new-capacity boundaries; verify all 256/512/1024/4096 owners, /GS cookies, BH stat ranges and ilvl, ordinary/magic/rare/unique/set/socketed/runeword items, and tall tooltip rendering. Remove probes before release.

**Current decision:** Root cause is qualified. A complete safe expansion is **not** yet qualified or implemented; two focused investigations remain. No production DLL has been patched or deployed by this PR.

## Reference artifacts

- [ProjectDiablo static root-cause audit and patch specification](projectdiablo-static-audit.md)
- [Phase 2 caller, scratch-buffer, hook, and BH qualification](qualification-phase2.md)
- [D2Client static analysis](d2client-static-audit.md)
- [ProjectDiablo verified patch-site manifest](projectdiablo-patch-sites.csv)
- [Parent caller allocation matrix](parent-caller-capacities.csv)
- [Original instruction-byte verification sample](binary-verification-sites.csv)
- [D2Client candidate call-site map](d2client-callsite-map.csv)
- [D2Client *debugger-only* conditional breakpoints](d2client-x32dbg-overflow-breakpoints.txt)

The CSVs are read-only research artifacts. **Do not** apply patch sites or treat x32dbg probes as a production hotfix. All addresses and byte checks are build-specific.