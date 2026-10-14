# Avatar, outfits and avatar shop

Client: `yo2.exe` Ver.080912 (static analysis in Ghidra). Data: the `merge.dat` of the 2008-11 client (an older
`merge.dat` exists whose `wm/ww_data.csv` differ; use the 2008-11 one).

Confidence markers: **CONFIRMED** = read in the code and checked again (second pass, see §9); **PROBABLE** = deduced
from the code without closing the last step; **DOUBTFUL** = assumption. §10 uses the short forms C / P / D.

Files:
- `proposal.py`: functions ready to integrate, with a self-test: `python tools/yo2emu/research/avatar/proposal.py`.
- The Ghidra script is `tools/yo2emu/ghidra/a4_multi.py`, which runs several jobs in one pass: `at`, `atj` (follows
  the thunk), `xref`, `callersj`, `str`, `strlist`, `scalar`, `rtti` (vtable from RTTI), `dis` and `bytes`.

## 0. The key points

1. The 16 bytes are **one byte per slot**. Each value comes from a fixed band: front hair 0x00, accessory 0x20, nose
   0x30, eyes 0x40, glasses 0x50, mouth 0x60, Domino clothes 0x70, Academia clothes 0x80, back hair 0x90, duel disks
   0xb0-0xd7. Bytes 13, 14 and 15 are colours. **CONFIRMED**
2. The catalogue of valid parts is **`data/common/yo2pbinmap.dat`**. The PBIN index is `value + 0x100·sex`.
   **CONFIRMED**
3. The client **validates** the parts (`FUN_00a58cc0`). A single non-existent part, a part from another slot or from
   the other world (Domino/Academia) is enough for the **whole avatar** to be replaced with the default boy avatar.
   That is why ordinary NPCs used to look wrong: with the original naive `npc_parts`, **54/54 failed in Domino and
   36/54 in Academia**. **CONFIRMED**
4. **PBIN** (128 B, 0x3114) is the inventory: 1024 bits, the first 512 being avatar parts. The editor only shows the
   parts you own. Sending 128 zero bytes means you do not even have the starter parts (55 bits). **CONFIRMED**
5. Shop: the only avatar item type is **2**, and its `param` is the **PBIN index**. Types 3-5 are not shown by any
   window. **CONFIRMED**
6. Buying a type-2 item does not set the bit in the client. The server must also send **0x5205 ITEMGET_RESULT** with
   the bit (PROBABLE) and store it for the next 0x3114 (CONFIRMED).
7. "Save avatar" is **0x4170 REGAVATARINFO**: `u32 world, 16 B, u32 0` → 0x4171. The client sends it when entering
   each block, and **the last one received is the current world**. **CONFIRMED**
8. There is no try-on message: the preview in the shop and in the editor is local. **CONFIRMED** (no client send
   carries parts except 0x4170)
9. **0x4212 (block_player) carries no parts** (`FUN_006b8b70`). The parts of the players on the map arrive through
   **0x4216 BLOCKPLAYERLIST2** and **0x4181**. **CONFIRMED**

## 1. The 16 bytes (`CAvatarData`, 0x14 B: vtable `0xbe9d78` + 16 B at +4..+0x13)

`FUN_009bce70` = `CAvatarData::Set(const u8[16])`: copies 4 dwords to `this+4`. The player has two avatars, Domino at
+0x134 and Academia at +0x148, so the parts are at +0x138 and +0x14c (disassembly at 0x68f8a8/0x68f8c7).
**CONFIRMED**

| Byte | What | Values (boy / girl) | Default | Bust: wm/ww category | Doll (chibi) | Source |
|---|---|---|---|---|---|---|
| 0 | **sex** | 0 = boy, 1 = girl (≥2 → invalid) | 0/1 | selects wm/ww | 0 → `FUN_009cde20`, 1 → `FUN_009cf060`; other value: not drawn | 9ccfc0, 9bced0, a58cc0 — CONFIRMED |
| 1 | front hair h1 | 0-13 / 0-9 | 0 | 11 | `h1_%02d_c%02d` (v+1, colour = byte 14) | 9eaa20 case 1, 9cf060 — CONFIRMED |
| 2 | face accessory a1 | 0x20 = none, 0x21 (plaster / cheeks) | 0x20 | 8 | `a1_%02d_c01` (v-0x20) | CONFIRMED |
| 3 | nose | 0x30, 0x31 | 0x30 | 6 | not drawn | CONFIRMED |
| 4 | eyes | 0x40-0x45 / 0x40-0x43 | 0x40 | 5 (ey), plus 3 (es) and 7 (eb) with the same value | `ey_%02d_c%02d` (v-0x3f, colour = byte 15) | CONFIRMED |
| 5 | mouth | 0x60-0x62 / 0x60-0x61 | 0x60 | 4 | `mo_%02d` (v-0x5f) | CONFIRMED |
| 6 | **clothes** | Domino 0x70-0x7c / 0x70-0x77; Academia 0x80-0x8b / 0x80-0x86 | 0x70 Domino, 0x80 Academia | 10 | dc/ac | CONFIRMED |
| 7 | back hair h2 | 0x90-0x93 / 0x90-0x92 | 0x90 | 0 | `h2_%02d_c%02d` (v-0x8f) | CONFIRMED |
| 8 | **duel disk** | Domino 0xb0-0xb7 and 0xc0-0xc4; Academia 0xb8-0xbf and 0xd0-0xd7 | 0xb0 / 0xb8 | 12 | not drawn (the chibi uses fixed `dd_01`/`ad_01` if +0x448 == 1) | CONFIRMED |
| 9 | glasses or eye patch a2 | 0x20 = none (0 becomes 0x20); 0x50-0x54 / 0x50-0x56 | 0x20 | 9 | `a2_..` (switch 'P'..'V') | CONFIRMED |
| 10-12 | unused | anything (not validated; `SetPart` rejects them) | 0 | — | — | a58cc0, 9eaa20 — CONFIRMED |
| 13 | skin colour | 0-2 (the validator accepts ≤7) | 0 | `bo/hs/es/no/mo` c01-c03 | `bo_01_c%02d` | editor: 3 options — CONFIRMED |
| 14 | hair colour | 0-6 | 0 | h1/h2 c01-c07 | h1/h2 `c%02d` | editor: 7 options (texts 0xcb2-0xcb8) — CONFIRMED |
| 15 | eye colour | 0-6 | 0 | ey/eb c01-c07 | ey `c%02d` | CONFIRMED |

Example: a character observed during testing had `00 00 20 30 40 60 80 90 b8 20 …` for Academia and `… 70 90 b0 …`
for Domino, which are exactly the default avatars.

**Default** (`FUN_009bced0(n)`, n = sex + 2·world): `[sex, 0, 0x20, 0x30, 0x40, 0x60, clothes, 0x90, disk, 0x20, 0…]`,
with clothes/disk = 0x70/0xb0 in Domino and 0x80/0xb8 in Academia. **CONFIRMED**

Special cases of the doll (`9cde20`/`9cf060`), all **CONFIRMED**:
- Boy front hair 6 (+1 = 7, "shaved"): the back hair becomes `h1_50` and it has no bust. Hair 10-13 (afro): the back
  hair is forced to `h2_11..14`.
- Girl front hair 5-9: the back hair is forced to `h2_06..10` (in `h2_09`, hair 8: ponytail).
- Boy clothes `'x'` 0x78 (qualifier's robe) and `'|'` 0x7c (Marik's outfit), and girl clothes `'u'` 0x75 and
  `'w'` 0x77: the hair is replaced with a hood (`h1_99`/`h1_98` and back hair `h1_50`).
- `FUN_00a58ec0` copies an avatar except clothes and disk (bytes 6 and 8). Used to go from one world to the other.
  PROBABLE (raised to C in §10)

### Validation (`FUN_00a58cc0(avatar, world, check_world)`) — CONFIRMED

Called by the map and NPC doll (`FUN_009ccfc0`, with check=1), the bust (`FUN_009e6e30`), `FUN_009f4fb0`,
`FUN_0080a2e0` and the editor (`CChangeAvatarPack`).
- byte 0 ≤ 1; bytes 13-15 ≤ 7.
- For each byte i = 1..9: `rec = yo2pbinmap[v + 0x100·sex]`. It must hold that `rec.slot == i`, or that
  `i ∈ {2, 9}` with `v == 0x20`.
- If the world is checked: `rec.world ∈ {1 (common), 2 if world = Domino, 3 if world = Academia}`.
- If anything fails, the whole avatar is replaced with `default(boy)` (with 0x80/0xb8 if world = 1). **A malformed girl
  shows up as a boy.**

## 2. Parts table (summary)

`yo2pbinmap.dat` (Shift-JIS CSV, 1024 rows; `FUN_00ad53d0`, record of 7 dwords at +0x70 + i·0x1c):
`pbin, value, starter, class, slot, text_id, [JP name], world`.
- Class: 1 = boy part, 2 = girl part, 10 = title (称号, 152-255 and 408-511), 4-9 = prize and NPC flags (544+). The
  shop also accepts a "class 3" (both sexes), but no row has that class.
- World: 1 = common, 2 = Domino, 3 = Academia.

| Slot | Boy (pbin = value) | Girl (pbin = 0x100 + value) | Starter |
|---|---|---|---|
| 1 hair | 0-13 (6 shaved, 7-9 quiff, 10-13 afro) | 0-9 (5-7 and 9 afro, 8 ponytail) | boy 0-5, girl 0-4 |
| 2 accessory | 0x20, 0x21 plaster | 0x20, 0x21 cheeks | 0x20 (girl: both) |
| 3 nose | 0x30, 0x31 | 0x30, 0x31 | all |
| 4 eyes | 0x40-0x45 | 0x40-0x43 | all |
| 5 mouth | 0x60-0x62 | 0x60-0x61 | all |
| 6 Domino clothes | 0x70-0x73 uniform (white, red, yellow, black), 0x74-0x76 tracksuit, 0x77 suit, 0x79 Yami Yugi, 0x7a Kaiba, 0x7b Joey, 0x7c Marik | 0x70 uniform, 0x71-0x73 punk, 0x74 suit, 0x76 Téa, 0x77 Marik | 0x70-0x73 / 0x70 |
| 6 common clothes | 0x78 qualifier's robe | 0x75 robe | — |
| 6 Academia clothes | 0x80-0x82 Red/Yellow/Blue, 0x83 tracksuit, 0x84-0x86 version 2, 0x87-0x8b Jaden, Chazz, Jesse, Kaiser, Aster | 0x80-0x86 | 0x80-0x82 |
| 7 back hair | 0x90-0x93 | 0x90-0x92 | all |
| 8 Domino disk | 0xb0-0xb7, 0xc0-0xc4 | same | 0xb0 |
| 8 Academia disk | 0xb8-0xbf, 0xd0-0xd7 | same | 0xb8 |
| 9 glasses | 0x50 sunglasses, 0x51-0x53, 0x54 eye patch (Bakura) | 0x50-0x55, 0x56 eye patch | — |

All of them have a bust (wm/ww) and a doll, except boy hair 6, which has no bust.

The editor removes the Academia Yellow (pbin 0x81/0x181) and Blue (0x82/0x182) uniforms depending on the player's byte
at +0xa4: below 10 neither Yellow nor Blue; from 10 to 19 no Blue (`FUN_008ae060`). It is the u8 that follows the name
in 0x3012. Whether it is the level or the rank is **PROBABLE**.

## 3. Avatar shop

- Item (0x5209, 17 B, `FUN_006c1c70` → vector +0x1194 of 20 B): `u32 id, u8 type, u32 param, u32 price, 4×u8`.
  **CONFIRMED**
- Each window keeps one type (vtable method +0x28). **CONFIRMED**:
  - `CShopCardWindow` (`FUN_00af0f90`): type 0.
  - `CShopPackWindow` (`FUN_00af53f0`): type 1.
  - `CShopAvatarWindow` (`FUN_00afac40`, created in `FUN_00af6f90`, category 2 of `FUN_00afd0d0`): **type 2**.
  - Types 3-5 are not used by any window.
- Type 2: `param` = **PBIN index**. The window reads the record (`FUN_00ad5920`) and shows it if:
  - class == player's sex + 1, or class 3;
  - and the world is 1 or the current one (2 in Domino, 3 in Academia).

  It marks "already owned" with the PBIN bit (`FUN_00ad6a90`). **CONFIRMED**
- The final 4 u8 still have no known use; they are sent as 0. **DOUBTFUL**
- The price was set by the server. The ones in `proposal.AVATAR_PRICES` are **invented** (300 hair, 1000 clothes,
  1500 disk…; ×3 for the celebrity gift clothes).
- Purchase: 0x5210 (`u32 id, u8 quantity`; the cart is at +0x11a4, entries of 0x1c) → 0x5212 (`s32` + 13 B entries
  `u32 id, u8 type, u32 param, u8, 3×u8`). With type 2 the client only adds the entry to the purchased list
  (`FUN_006c43d0`); **it neither adds cards nor sets the bit**. Then 0x5211 `s32` and 0x4140 with the balance arrive.
  **CONFIRMED**
- For the garment to become yours:
  - **0x5205 ITEMGET_RESULT** = `u16 n, n×u16 PBIN bits (FUN_00ad6900(i, 1) + item notice), u16 m, m×u16 cards`.
    The format is **CONFIRMED**; it is **PROBABLE** that it can be sent unsolicited (the handler checks no state; it
    only sets +0x7f = 2).
  - At the next login, the 0x3114 with the stored bit. **CONFIRMED**
- Preview: the window creates a `CWaistUpAvatar` (`FUN_009e2c40`) with the default avatar or the player's, and applies
  the part to it (`FUN_009eaa20(slot, value)`). Everything is local. **CONFIRMED**

## 4. PBIN (inventory of parts, titles and flags)

- 0x3114 GETPBIN, answered by the **account** server (dispatcher `0068eca0`, port 5731): 128 B, which `FUN_00ad68a0`
  copies as is. **CONFIRMED**
- Bit i = `byte[i >> 3] & (1 << (i & 7))`, least significant bit first (`FUN_00ad6a90`, `FUN_00ad6900`).
  **CONFIRMED**
- Starter bits: the 55 rows with `starter = 1` of classes 1 and 2 (`proposal.default_pbin_bits()`). It is
  **PROBABLE** that the original server sent them set. What is CONFIRMED is that the editor lists only parts whose
  bit is set (`FUN_00ad5ab0`). `CChangeAvatarPack` (`FUN_008b07d0`) enables a minimal set in its copy (0-5, 0x20,
  0x30-0x31, 0x40-0x43, 0x60-0x62, 0x70-0x73, 0x80-0x82, 0x90-0x93, 0xb0, 0xb8): this is character creation.
- Other messages that set bits: 0x4903 PURCHASE_PACK_OPEN_RESULT (0x302-0x307), 0x8102 and 0x8204 (DP auth), 0x5205.

## 5. Outfit and avatar messages

| Id | Direction | Format | What it does | Confidence |
|---|---|---|---|---|
| 0x4170 REGAVATARINFO | C→S | `u32 world, 16 B, u32 0` | Registers the avatar. Sent in states 0xd/0xf of `FUN_006d1840` when entering each block: in My Room (b2 = 12) first the other world's avatar, then the current one; in other rooms only the current one. It is also the way to save after the editor | CONFIRMED (when it is sent after the editor: PROBABLE; raised to C in §10) |
| 0x4171 | S→C | `s32` | Stored at +0x80c without checking; releases the wait (+0x27) | CONFIRMED |
| 0x4181 UPDATE_PLAYERAVATARINFO | S→C | `u32 player id, u32 world, 16 B` | Changes the avatar of a player in the block list (+0x134 + world·0x14) and requests a refresh | CONFIRMED |
| 0x3012 PLAYERLIST | S→C | 76 B entry: …, `16 B Domino, 16 B Academia` (the last two fields) | The character's saved avatars | CONFIRMED |
| 0x4214 → 0x4215/0x4216/0x4217 | | 0x4216: `[u32 id, u8 type, 16 B, u16, u16, u8, (if type ∈ 'g'..'j': 16 B, u8, 3×u16)]…` | Avatar (current world) of the players in the block | format CONFIRMED; that the player's own avatar comes from here is PROBABLE |
| 0x43a0 AUTODUELSTART / 0x4302 MATCHOFFER | S→C | the 2nd 16 B block is the opponent's avatar (+0xb28) | Opponent's bust in the duel | destination CONFIRMED; on-screen effect PROBABLE |
| 0x3114 GETPBIN | C→S `u8` / S→C 128 B | above | Inventory | CONFIRMED |
| 0x5205 ITEMGET_RESULT | S→C | above | Grants PBIN bits and cards | CONFIRMED |
| 0x5204 ITEMGET | C→S | `u16 type, u32 value` | Requested by NPC prizes (`FUN_0089dbf0`…, `FUN_009fcca0`, `FUN_00850d90`); if type is 4, the client also marks its local table (`FUN_00ad6d10/6d80`) | format CONFIRMED; usage PROBABLE |
| 0x4174 UPDATE_PLAYERTITLE | C→S | `s32` | Title (class 10 pbins) → 0x4175 `s32` | PROBABLE |

There is no try-on message. The editor is `CWindowDialogAvatarSwitch` (`FUN_008a88f0`/`FUN_008ae060`, with tabs for
sex, colours, hair, eyes, nose, mouth, skin, clothes, accessories and disk). It is opened by `CChangeAvatarPack`
(`FUN_008b0f70`), inside scene `FUN_008b46b0`. That scene opens its own `CNetAccountTask` + `CNetLobbyTask` and edits
the player's two `CAvatarData` (+0x134/+0x148) with its PBIN (+0x3264).

## 6. Ordinary NPCs

- Ids < 51 or > 94: `FUN_009d7bf0` calls `CChibiAvatarElm` `FUN_009c9aa0(record + 0x15)`, which sets mode 0 (avatar).
  They therefore go through `FUN_009ccfc0` and **the same validation** with the current world. Ids 51-94: fixed doll
  (`FUN_00a55cd0`). **CONFIRMED**
- The original naive `npc_parts` always used disk 0xb8 (Academia) and clothes picked at random between Domino and
  Academia; for girls it also used mouth 0x62/0x63 and back hair 0x93, which do not exist. Result: **54/54 defaulted in
  Domino and 36/54 in Academia**.
- `proposal.npc_parts(nid, sex, world)` picks from safe lists (no shaved hair, hoods or celebrity clothes). Face, hair
  and colours are fixed per id; clothes and disk depend on the world. 54/54 valid in both worlds and all different.
- The world is the `u32` of the **last** 0x4170 on that connection: 0x4170 arrives before 0x4230. **CONFIRMED** by the
  code (state order) and by recorded sessions. In the Beginners' Room (5745) the client ends up in Academia (world 1).
- Sex: 'M'/'F' from the NPC id table; if missing or '?', a fixed split by id.

## 7. Integration (summary; details in the header of `proposal.py`)

The module is loaded by the server as `AV` (`yo2srv/arranque.py`; avatar handling in `yo2srv/mundo/avatar.py`).

1. **0x3010**: `player_entry_with_avatars` instead of `player_entry`.
2. **0x3114**: `AvatarStore.pbin_payload()` (starter bits plus purchased ones).
3. **0x4170**: store the parts per world, set `c.world = world` and answer 0x4171 0. With several players, also 0x4181
   to the others.
4. **0x4214**: 0x4215 `u32 0` + 0x4216 `block_player2_payload([(PLAYER_ID, parts of the current world)])` + 0x4217.
5. **0x4230**: parts of ordinary NPCs computed with `npc_parts(nid, sex, c.world)` at send time.
6. **Shop**: add `avatar_shop_items(len(base) + 1)` to the catalogue. In 0x5210, if the type is 2:
   - call `purchase_avatar` (not `shop_purchase`, which would treat it as a pack);
   - `set_wallet`;
   - `AvatarStore.add_bit`;
   - send 0x5212, 0x5211, 0x4140 and 0x5205.
7. **0x4288** (optional): `autoduelstart_payload(opponent, name, npc_parts(...))`.

Also: the `.iid/.ydc` pack files should come from the 2008-11 data (`data/cardpack` has 298 files there versus 257 in
the older `merge.dat`; `packinfo_s.txt` is identical).

## 8. Suggested live test

1. Integrate points 2-5, enter the Beginners' Room and check in the log that the last 0x4170 carries `00000001`
   (Academia). Ordinary NPCs must have varied clothes and hair, and girls must look like girls, not like the repeated
   default boy.
2. Open the shop. The avatar tab must show garments of your sex and of Academia/common, and starter parts are not on
   sale.
3. Buy sunglasses (pbin 0x50). The log must show 0x5212, 0x5211, 0x4140 and 0x5205 (`0001 0050 0000`). The garment
   must show "already owned" when reopening.
4. Open the avatar editor (My Room or the change-avatar menu), put the glasses on, save and see the 0x4170 with
   byte 9 = 0x50. After reconnecting, 0x3012 must return it.

## 9. Internal verifier (second, adversarial pass)

| Statement | How it was checked again | Result |
|---|---|---|
| Parts at `this+4` and avatars at +0x134 (Domino) and +0x148 (Academia) | Disassembly of `68f8a8` (`LEA ECX,[EBP+0x134]`) and `68f8c7` (`+0x148`); 0x4170 pushes `+0x138 + world·0x14` (`6d1cb0`). Matches the bytes in the log | CONFIRMED |
| Byte → bust category | Disassembly of `9eab00`: `PUSH value; PUSH 0xb; PUSH sex; CALL lookup`, which writes +0x1bb5 (= byte 1) | CONFIRMED |
| yo2pbinmap columns | Decompiled loader `FUN_00ad53d0` (columns 0-5 and 7 → 7 dwords), cross-checked with the uses in a58cc0 (slot = dword 4, world = dword 6) and in afac40/ad5ab0 | CONFIRMED |
| Validation rules | Disassembly of `a58cc0` (`CMP [ESP+0x3c],ESI` = slot; `CMP EAX,1` / `CMP EAX,[ESP+0x14]` = world; exception `CMP BL,0x20` for i = 2/9) | CONFIRMED |
| The avatar shop = type 2 | +0x28 methods of the three windows (`==0`, `==1`, `==2`); their vtables come from RTTI, and the `CShopAvatarWindow` constructor is instantiated by `af6f90`, created in category 2 of `afd0d0` | CONFIRMED |
| PBIN bit order | `ad6a90`/`ad6900` | CONFIRMED |
| Message sizes | `proposal.py` against the client message table (12 sizes; 0x4216 has a conditional part that the static table always counts) | ok |
| npc_parts | The self-test validates the 108 combinations (54 NPCs × 2 worlds) with a replica of a58cc0 | ok |

Still **DOUBTFUL**:
- The final 4 u8 of the shop item.
- The meaning of the u16/u8 of 0x4216 and of type 'g'..'j'.
- Whether the original server set the starter bits in PBIN (the simplest is to send them).
- Whether sending 0x5205 unsolicited opens an "item obtained" window: harmless, but to be checked live.

## 10. My Room, and one look per world

Question: in My Room, did the character appear with their usual look? Did the outfit apply to both Domino City and
Duel Academia, or did each world have its own?

**Short answer.** The character has **two** saved looks (one per world, +0x134 Domino and +0x148 Academia), but **only
the clothes (byte 6) and the disk (byte 8) are per world**: hair, face, glasses and colours are shared, because when
saving, the editor copies everything except clothes and disk to the other world. My Room belongs to no world: it shows
the look **of the world you came from** (Domino on first entering the game). And yes, the character appeared with
their usual look: an "orange hair" look seen during development was the **default avatar**, caused by a server bug
(fixed, see below).

| Fact | Evidence | Confidence |
|---|---|---|
| The current world is `DAT_012bb6b0+0x448` (0 Domino, 1 Academia). Set by `FUN_00a58740` with `FUN_00a587a0(block type)`: types 1, 9, 10, 11, 0xe, 0x10, 0x11, 0x14 → 1; 7, 8, 0xd, 0xf → 0; **0xc (My Room) → unchanged** | decompilation of `a587a0` and its calls (`64f06c`, `7710a9`, `a332f5`: `push [x+0xbba3]` → `a587a0` → `a58740`) | C |
| On entering the game My Room is in Domino (world 0) | recorded session: in the first My Room both clients send 0x4170 world 1 and then world 0 (the last one is the current one, `FUN_006d1840` states 0xd/0xf) | C |
| My Room doors: `CTmAvatarOnExit` ExitNo 0 and the `go_legacy_auto_btn` button → `FUN_00829c10` (block 0xd, Domino); ExitNo 1 and `go_gx_auto_btn` → `FUN_00829cd0` (block 0xe, Academia). `CTmAvatarOnExit` means "the doll steps on an exit"; it does not change the look; the world changes when entering the destination block | `FUN_00825720` cases 0x13 and 0x1d (source `src/MyRoom…`) | C |
| The player's own doll in My Room is created by `FUN_009be6b0` (`CAvatarNetSupport`) when 0x5340 arrives: it looks up the pid in the block list (`+0xc6c → +0x9a4`) and calls `FUN_009d2930` → `FUN_009de490`, which uses **the parts of that record**, `rec+0x134 + world·0x14`. It does not read the character record (`+0xc68 → +0x124c`, the one from 0x3012) nor 0x4101 | decompilation of `9be6b0`, `9d2930` (disassembly `9d2b2f-9d2b4a`), `9de490` | C |
| LIST1 (0x4212) creates the record with `FUN_006c33d0`, whose two `CAvatarData` start as `FUN_0063c250` = `FUN_009bced0(0)` (default Domino boy). Only LIST2 0x4216 (`FUN_006b8eb0`, the receiver's world), 0x3112 op 1 and **0x4181** (`FUN_006b9930`, `rec+0x134 + world·0x14`, disassembly `6b99b8-6b99cb`, without checking the world) give it parts | decompilation | C |
| In My Room the client does not request LIST2 (nor 0x4210/0x4214): the player's own record kept the default avatar. Seen in Domino: hair 0 colour 0 (`cm_h1_01_c01`, orange) and uniform 0x70 (`cm_dc_01_c01`, blue) = "blue outfit"; seen in Academia, 0x70 is not valid and `FUN_00a58cc0` replaces it with the Academia default: uniform 0x80 (`cm_ac_01`, red) = "orange hair and red jacket". It was not the saved look with lost colours: it was the default one | mean colours of the PNGs in `data/avatar/chibi`; log (in My Room only 0x4211/0x4212/0x4213, no 0x4216/0x4181) | C |
| In My Room the client registers **both** looks (0x4170 of the other world, then of the current one) **after** LIST1 and **before** 0x5320 | `FUN_006d1840` states 0xd/0xf (`b2 == 0xc`); order in the log | C |
| Most likely the official server answered each 0x4170 with 0x4181 UPDATE_PLAYERAVATARINFO to the whole block, the sender included (in My Room the block has a single player): it is the only message with the same `u32 world + 16 B`, and it would explain why My Room sends both worlds | deduction | P |
| Editor (`CChangeAvatarPack` `FUN_008b07d0`, window `CWindowDialogAvatarSwitch`): receives the character's two `CAvatarData` (+0x134/+0x148), the PBIN and the current world (+0x358 = `+0x448` when opened; it does not change inside). The look of that world is edited and, on accept (result 0x1b), `FUN_00a58ec0` copies **everything except bytes 6 (clothes) and 8 (disk)** to the other world. When changing sex, the other world gets a default avatar of that sex | decompiled client code | C |
| Saving = `FUN_006d3d10` puts the lobby task in state 0xd: it sends 0x4170 again (in My Room, both worlds). This raises to C what §5 marked as "PROBABLE" | `006d3d10`; case 2 of `FUN_008b46b0` | C |

**Server fix** (`MYROOM_SELF_AVATAR` in `yo2srv/mundo/avatar.py`, on; `YO2_MYROOM_AVATAR=0` turns it off): in My Room
(`LOBBY_PORT`), after the 0x4171 for each 0x4170, the server sends the client itself `0x4181 = u32 pid, u32 world,
16 B` with the parts it has just registered (worlds 0 and 1 only). The block record thus has both looks before 0x5340,
and the doll shows the current world's look. Rooms (maps) are unchanged: there LIST2 already includes the player
itself (`pvp_hub.LIST_SELF`).

Still **D**: whether, after saving in the editor, the My Room doll is redrawn by itself with the new 0x4181
(`FUN_009d3820` does not re-apply parts; the scene may be rebuilt when returning from the editor and request another
0x5320). To check live: change hair in My Room and see whether it shows without leaving.
