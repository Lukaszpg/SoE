# PD2 Season 14 / SoE — D2Client tooltip buffer investigation (phase A, static)

Date: 2026-11-17  
Status: **Original 1.13c binary statically qualified; no DLL modifications; no in-game runtime trace yet.**

## Binary identity / scope

- D2Client.dll: SHA-256 `dd8bc6025de921216a97c17f97cd1a50fbb85926e838ec60e13451448836d906`; image base `0x6FAB0000`.
- D2Common.dll: SHA-256 `6c350a82e0469519d51b21988e78134758dfa2aed58bd83674b49bfa6f786954`.
- BH.dll: SHA-256 `4173b0c2146c0001ebecfb8b10be73915daf3b933f1fd6e05b71f11992692998`.

All `D2Client+0x...` locations below are **RVAs**, not process addresses. The files have been verified against the supplied bytes. PD2 may patch original instructions after loading, so verify the disassembly of the running process at breakpoints before relying on them.

## Confirmed findings

### F01 — Stat-description formatter ignores the passed output capacity

`D2Client+0x560B0` sets up an intermediate 1,024-entry UTF-16 buffer and calls `D2Client+0x55B20`:

```asm
D2Client+0x560B0  sub esp, 808h          ; 0x808 bytes of local stack
D2Client+0x560BD  mov ebx, 400h          ; initialize 1024 UTF-16 elements
D2Client+0x560E6  push 400h              ; passed capacity
D2Client+0x560FB  call D2Client+0x55B20
```

Inside `D2Client+0x55B20`, the capacity occupies the third stack argument (`[esp+0x12D8]` after its entry stack allocation and saved-register pushes). There are **no accesses to this argument in the inspected function body**. Instead the formatter calls the imported `D2Lang!Unicode::strcat` repeatedly, including through EDI at `D2Client+0x55E1D` and other sites. This is concrete evidence that the passed size is not applied as a bound to its output writes in this version.

A one-character limit increase in the caller would only change its assertion about capacity, not the size of its local stack storage or the formatter's unbounded appends.

### F02 — Main assembly destination is smaller than modifier source

`D2Client+0x93450` reserves a `0x6650`-byte stack frame. It initializes:

| Buffer | ESP-relative start after prolog | UTF-16 capacity |
|---|---|---:|
| Main assembled description | `esp+0x60` | 1024 |
| Final popup text | `esp+0xA60` | 1024 |
| Magic modifiers | `esp+0x4660` | 4096 |

The caller at `D2Client+0x94170` passes the 4096-entry modifier destination to `D2Client+0x560B0`. Later, at `D2Client+0x9426F`, native code appends text into the **1024-entry main assembled description**:

```asm
D2Client+0x9425B  push 3
D2Client+0x9425D  lea edi, [esp+0x4664] ; effective start esp(before push)+0x4660
D2Client+0x94264  call D2Client+0xBF5F0 ; color-prefix helper
D2Client+0x94269  mov edx, eax          ; source = magic-modifier text
D2Client+0x9426B  lea ecx, [esp+0x60]   ; destination = main 1024-entry buffer
D2Client+0x9426F  call ebx               ; EBX was loaded from IAT Unicode::strcat
```

`D2Client+0x9375A` loads EBX from IAT RVA `0xCEABC`; the PE import descriptor identifies that slot as `D2Lang.dll!Unicode::strcat` (`?strcat@Unicode@@SIPAU1@PAU1@PBU1@@Z`). The import takes no buffer-size argument.

This is a **concrete, statically reachable overflow path** whenever source + destination contents require more than 1024 UTF-16 elements. It is **not** proof that a particular crashing item follows that path, because no matching crashing-item runtime trace has yet been captured.

### F03 — Main-to-popup copying and appended notes are also unbounded

At `D2Client+0x94426` the code prepares the 1024-entry final popup destination and copies the assembled description via `Unicode::strcpy` at `+0x94431` (ordinary path); additional branches can copy and append into the popup (`+0x944D5`, `+0x944E2`). This final buffer must be expanded in tandem with the main buffer. Enlarging only the main destination will move the overflow into the final popup.

### F04 — Final length clamp is too late

`D2Client+0x94509`: `cmp eax, 0x3FF`; `+0x94510`: write terminator at popup character index 1023. Because this occurs **after** the concatenations and copies, it cannot repair memory already overwritten.

### F05 — Multiple upstream and alternate buffers require review

There are 1,024-character intermediates and additional 128/256-character stat-line buffers. The 1.13c code contains alternate branches for set items, socketed items, NPC vendor prompts, and other item types. The supplied BH.dll additionally modifies tooltip text; its matching source is maintained in `Lukaszpg/SoE`. These must be checked before changing the capacity of exported callbacks.

## Runtime qualification kit

Files supplied alongside this report:

- `D2Client_Tooltip_Overflow_x32dbg.txt`: 36 **candidate** x32dbg breakpoints (31 `strcat` sites and 5 `strcpy` sites), each with a conditional UTF-16 length check. These are static call-site candidates, **not a complete proof that every write is covered**. The script has not been executed in a Windows debugger here.
- `D2Client_Tooltip_Callsite_Map.csv`: matching addresses, exact opcode bytes, stage, and tested capacity.

### Running the probe

1. Back up your character and use an isolated single-player PD2 test installation. Launch the **32-bit** game in x32dbg; ensure `D2Client.dll` has loaded and use the matching DLL SHA-256 above.
2. At a paused debugger command prompt, execute `scriptexec C:\Path\To\D2Client_Tooltip_Overflow_x32dbg.txt` (replace the path).
3. Continue, hover the known crashing item. Conditional breakpoints should stop **before** a monitored write with `len(dest)+len(src)>=1024` for a `strcat`, or `len(src)>=1024` for a `strcpy`.
4. Record the stopped **RVA**, registers `EIP/ECX/EDX/ESP`, `strlen(utf16(ecx,0x400))`, `strlen(utf16(edx,0x1000))`, the call stack, the exact native bytes at that instruction, and the item name.
5. Do not step over the suspected overflowing operation. If there is no conditional break but the game still crashes, capture the true faulting EIP and call stack instead; the failure could be in an unmonitored buffer or a different mechanism.

`x32dbg` expressions are case-insensitive. It defines `utf16(address, maximum_count)` and `strlen(string)`; both are documented at https://help.x64dbg.com/en/latest/introduction/Expression-functions.html. All numeric values in the script have explicit `0x` hexadecimal prefixes. Source/destination pointers at these sites are in `EDX`/`ECX`.

This probe does **not** patch DLL bytes, set runtime interception, or persist logs in production. It only sets debugger breakpoints. High per-frame breakpoint counts can temporarily slow hovering; remove them after qualification.

## Gate for implementation

Do not apply an expanded native tooltip DLL until (1) the first offending write is observed and its original bytes verified in the running PD2 client, (2) the complete old/new storage layout is accounted for (including branches and frame references), and (3) the BH output pointer contract is aligned with the native destination sizes. A production patch that merely changes `0x3FF`, `0x400`, or enlarges a single buffer is unsafe.

**Suggested initial targets (provisional):** 4096 UTF-16 elements for the stat-description intermediate, 8192 elements each for main and final output, with bounded writes. Reassess sizing after measuring the actual crashing item. Do not modify game behavior or truncate modifiers simply to make the test pass.