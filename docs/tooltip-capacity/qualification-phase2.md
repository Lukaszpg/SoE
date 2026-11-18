# PD2 Season 14 — Tooltip capacity qualification, phase 2

**Date:** November 18, 2026  
**Scope:** uploaded `ProjectDiablo-SoE-23IndexExclusions.dll`, `D2Client.dll`, `BH.dll`, and source of `Lukaszpg/SoE` branch `feature/soe-tooltip-bh-capacity-prep`. No native files were modified.

## Bottom line

**The five direct calls to `ProjectDiablo+0x2B43C0` are now traced to buffers of known sizes.** This resolves the main previously unknown direct-caller allocations. It does **not** yet qualify a production-ready expansion: some consumers are far smaller than 4,096 UTF-16 characters, the formatter's per-stat temporary buffers have separate limits, and three D2Client callback patches require exact runtime integration checks.

All addresses in this report are **preferred-image virtual addresses (VA)** based on `ProjectDiablo.dll` loaded at `0x10000000`, unless otherwise stated. ProjectDiablo RVAs are obtained by subtracting `0x10000000`. `D2Client.dll` has preferred image base `0x6FAB0000`. Do not confuse hexadecimal `B` and `8`.

## 1. Direct parent caller destination capacities — newly qualified

| Call to `0x102B43C0` | Destination | Physical capacity (UTF-16 code units) | Allocation evidence |
|---|---|---:|---|
| `0x102ADDEB` | `[EBP-0x80C]` | **1,024** | `0x102ADD9F` initializes `0x7FE` bytes beginning two bytes beyond the first character; `0x102ADDE4` zeroes first WCHAR |
| `0x102ADEC4` | `EBX`, external | **512 or 1,024** for traced call paths | `0x102ADEA0` forwards an output pointer; `0x102ADF20` is invoked by `0x102F34D1` (512) or `0x102AE857` (1,024) |
| `0x102AFEAB` | `[EBP-0x604]` | **256** | `0x102AFDA3` initializes `0x200` bytes; next buffer starts at `[EBP-0x404]` |
| `0x102B180A` | `[EBP-0x3E88]` | **1,024** | `0x102B0F18` initializes remaining `0x7FE` bytes; first WCHAR initialized separately; neighboring buffer at `[EBP-0x3688]` |
| `0x102B3121` | `[ESP+0x6048]` | **4,096** | `0x102B2268` initializes `0x2000` bytes of that buffer |

The external branch is now resolved for its known direct paths:

- `0x102ADEC4` ← helper `0x102ADEA0` ← `0x102ADF20` ← `0x102F34D1`, where the owning output buffer at `[ESP+0x540]` is initialized with `0x400` **bytes** at `0x102F3418`: **512 UTF-16 characters**.
- `0x102ADEC4` ← helper `0x102ADEA0` ← `0x102ADF20` ← `0x102AE857` (`0x102AE800`), where the buffer at `[ESP+0x5848]` is initialized with `0x800` **bytes** at `0x102B223A`: **1,024 UTF-16 characters**.

**Consequence:** We cannot simply enlarge the 1,024-character parent buffer to 4,096 and allow its output to flow into these 256/512/1,024-character destinations. They must each gain a real capacity contract and, where appropriate, their own larger allocation, plus all of their consumers must be checked.

## 2. Formatter arguments and scratch buffers

Function `0x102B4750` receives output pointer at `[EBP+0x10]`, capacity at `[EBP+0x14]` and returns with `ret 0x20`. It has known direct callers requesting **512**, **1,024**, and **1,024** UTF-16 characters at `0x102B1885`, `0x102B441C`, `0x102F345D`; also receives calls via forwarding thunk `0x103A4D60`.

The formatter's local frame is allocated with `EAX=0x24DC; call 0x103B89C0` (stack-probing helper), and includes:

- Two `0x200`-byte initialized scratch areas (256 UTF-16 code units each) near `[ESP+0x20E0]` and `[ESP+0x22E0]` after stable stack adjustment.
- A `0xA8`-byte auxiliary area.
- A `0x1FF8`-byte stat-pair area.

The eight output `Unicode::strcat` calls (`0x102B494D`, `0x102B4AAC`, `0x102B4AB7`, `0x102B4ACA`, `0x102B4B72`, `0x102B4B7D`, `0x102B4B8A`, `0x102B4B9B`) do not enforce `[EBP+0x14]`. The separate call at `0x102B4A8F` writes to **local scratch**, and requires a distinct scratch-capacity check. Both `0x102C49A0` and `0x102C49E0` forward individual stat descriptions to BH callbacks (when BH is loaded). This creates a separate per-line capacity boundary that cannot be fixed by expanding the accumulated output alone.

**Not yet demonstrated:** every path through per-stat scratch formatting and BH range augmentation stays within its true 256-character temporary destination. A larger aggregate output buffer does not cure a per-line overflow.

## 3. Indirect native caller qualification

`0x103A4D60` forwards eight arguments to `0x102B4750` (`ret 0x1C`). Its address is written into callback/patch records at:

- `0x104A7170` from `0x101212E9`
- `0x104A7184` from `0x1012131B`
- `0x104A7198` from `0x1012134D`

The associated patch target RVAs are **D2Client+`0x55F9C`, `0x56038`, `0x56098`**. Significantly, these are the **four-byte displacement operands** of native `CALL rel32` instructions at **D2Client+`0x55F9B`, `0x56037`, `0x56097`**, rather than stand-alone function entry addresses. This is verified by disassembly of the uploaded original D2Client DLL.

The forwarding ABI is therefore a production integration surface, not an irrelevant duplicate function. Native `D2Client+0x560B0` also uses a 1,024-character intermediate and a native stat formatter. **The exact maximum output size and every receiving buffer for the three PD2-patched native call paths still require runtime or full cross-module parameter tracing.** The installed DLL contains original prepatch bytes, and runtime PD2 hooks are applied later, so disk-only analysis cannot certify the final in-memory patch state.

## 4. BH linkage and capacity policy

`ProjectDiablo+0x2C4760` loads `BH.dll` and resolves callbacks with `GetProcAddress`. Mapping the string addresses and stored pointers confirms:

| Native entry | BH export | Contract |
|---|---|---|
| `0x102C49A0` | `_BHOnDamagePropertyBuild@16` | Per-stat property buffer; no capacity parameter |
| `0x102C49E0` | `_BHOnPropertyBuild@16` | Per-stat line scratch; no capacity parameter |
| `0x102C4970` | `_BHOnProperties@4` | Assembled description; no capacity parameter |

`Lukaszpg/SoE` draft PR #1 defines `ITEM_TOOLTIP_BUFFER_CAPACITY=1024`, `ITEM_PROPERTY_BUFFER_CAPACITY=1024`, `MAX_ITEM_TEXT_SIZE=512`. This is **preparatory and intentionally unchanged**. The BH exported callbacks receive raw pointers and must be given an explicitly qualified destination contract before increasing any of those constants. The per-stat callbacks and whole-tooltip callback should not be conflated.

## 5. Downstream D2Client limits remain independent

The uploaded original D2Client 1.13c binary also contains:

- `D2Client+0x560B0`: **1,024**-character intermediate, passed to `+0x55B20`.
- `D2Client+0x93450`: main assembly buffer **1,024**, final popup buffer **1,024**, separate magic-modifier buffer **4,096** characters.
- `D2Client+0x9426F`: calls unbounded `Unicode::strcat` into the 1,024-character main buffer.
- `D2Client+0x94431`: copies the main description into the 1,024-character popup.
- `D2Client+0x94509`: final length clamp occurs **after** concatenation; insufficient protection.

These are independently verified static constraints, not evidence that your demonstrated runtime failure originated in D2Client. Your known overflowing write was at `ProjectDiablo+0x2B4ACA` in the ProjectDiablo output formatter.

## 6. Remaining implementation gates (precise)

| Gate | Status | What must be completed |
|---|---|---|
| Parent caller ownership | **Qualified for all known direct paths** | Include 256 / 512 / 1024 / 4096 sizes in replacement contract and independently check any indirect callers |
| Formatter output size | **Qualified broken** | Design a bounded append that checks the actual argument, retains whole lines and proper color-code integrity, and propagates overflow rather than silently truncating |
| Formatter scratch / BH property callbacks | **Partially qualified** | Check all formatters for the 256-WCHAR temporary areas, including range expansion and each BH callback, with known input lengths |
| Three D2Client callback patches | **Partially qualified** | Trace the in-memory `CALL` targets and output lengths after ProjectDiablo patch registration for the exact S14 build |
| Final native D2Client / BH capacity | **Constrained but not expanded** | Establish new allocations and all references before changing capacity constants or branch checks |
| Runtime validation | **Pending implementation** | Test boundaries, original crashing item, BH range/ilvl toggles, set/runeword/socketed items and renderer height |

## Decision

The **direct parent-caller allocation qualification is complete**. The next focused qualification is **per-stat scratch/BH callback safety and three runtime-patched D2Client call boundaries**. These cannot be declared fully safe on the current static evidence alone.

Once those are established, implementation should modify the **original ProjectDiablo formatter and native tooltip assembly path** in coordination; do not introduce a replacement renderer, skip overflowing modifiers, or bypass stack-cookie checks. No DLL changes are included in this phase.

## Reproduction / binary identity

| File | SHA-256 |
|---|---|
| ProjectDiablo | `85e20da4d680519381499997f3443194e01691a41202c0e2faf5347c3e1dbebb` |
| D2Client | `dd8bc6025de921216a97c17f97cd1a50fbb85926e838ec60e13451448836d906` |
| BH original upload | `4173b0c2146c0001ebecfb8b10be73915daf3b933f1fd6e05b71f11992692998` |

The accompanying checker validated **31 exact instruction-byte sites** against these DLLs; it is a read-only check and cannot establish live patched-code integrity.