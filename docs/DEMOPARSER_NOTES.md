# demoparser2 — working notes (CS2-AimAudit)

> This file is a "crash course" for any new session: everything you need to
> know about the CS2 demo parser to start working on the project right away.
> Sources: the LaihoE/demoparser repository README (read fully),
> documentation/python/README.md (read fully), examples/ — almost all read
> (see section 8).

## 1. What it is and how it's built

- Repository: https://github.com/LaihoE/demoparser
- Heavy lifting in Rust, wrappers: Python (`pip install demoparser2`, py>=3.8),
  NodeJS (`@laihoe/demoparser2`), WASM.
- Philosophy: NOT streaming events — "query the demo like a database".
- Performance: 50 demos (4.6 GB, MM+Faceit+HLTV) in ~6–14 s (749/328 MB/s).
- Author's Discord: https://discord.gg/rNUjS4vfnX
- Other parsers (for reference): demoinfocs-golang (Go), demofile-net (C#), awpy (Python), clarity (Java).

## 2. Python API — signatures (official docs, verbatim)

```python
from demoparser2 import DemoParser

parser = DemoParser("path_to_demo.dem")

parser.parse_header() -> Dict[str, str]
parser.parse_convars() -> Dict[str, str]
parser.Sequence_game_events() -> List[str]
parser.parse_chat_messages() -> pd.DataFrame
parser.parse_player_info() -> pd.DataFrame
parser.parse_item_drops() -> pd.DataFrame
parser.parse_skins() -> pd.DataFrame
parser.parse_grenades(extra: Optional[Sequence[str]] = None, grenades: bool = True) -> pd.DataFrame
parser.parse_weapons(extra: Optional[Sequence[str]] = None, ticks: Optional[Sequence[int]] = None) -> pd.DataFrame
parser.parse_event(event_name: str, player: Optional[Sequence[str]] = None, other: Optional[Sequence[str]] = None) -> pd.DataFrame
parser.parse_events(event_name: Sequence[str], player=None, other=None) -> List[Tuple[str, pd.DataFrame]]
parser.parse_voice() -> Dict[str, bytes]
parser.parse_ticks(wanted_props: Sequence[str], player: Optional[Sequence[int]] = None, ticks: Optional[Sequence[int]] = None) -> pd.DataFrame
parser.list_game_events() -> List[str]   # roughly as slow as parse_event
```

### parse_event / parse_events

- Returns a DataFrame of events of the given type.
- `player=["X", "Y"]` — "player" props are added to the event with the
  participant prefix: for `bomb_planted` you get `user_X`, `user_Y`.
- `other=["total_rounds_played"]` — GAME state (only game-state props are
  valid in `other`!). Our core.py calls
  `parse_events(["player_death", "weapon_fire", "player_hurt"], other=["total_rounds_played"])`
  — this is correct usage: `total_rounds_played` = m_totalRoundsPlayed.
- `parse_events([...])` returns a list of `(event_name, df)` tuples —
  we turn it into `dict(...)`. The order of events in the list is not
  guaranteed by the docs — so we look up by key, not by index.

### parse_ticks

- Per-player props per tick. `ticks=[983]` — which ticks; without the arg —
  all ticks.
- `player=[steamid:int, ...]` — player filter (int!). We pass
  `players=[int(me)]` in timeline_data.
- Always returns columns `tick`, `steamid`, `name` + the requested props.
- IMPORTANT (our experience, not from the docs): UInt64 columns with nulls
  turn steamid into float64, and SteamID64 (>2^53) gets ROUNDED — we fix it
  via a lookup from parse_player_info (core.fix_ids).

### parse_player_info

Example output:

```
steamid          name     team_number
76561111111111111 player1  2          # 2 = T, 3 = CT
```

Updating the steamid field in parse_ticks — see above (fix_ids).

### parse_header

Fields: `addons, server_name, demo_file_stamp, network_protocol, map_name,
fullpackets_version, allow_clientside_entities, allow_clientside_particles,
demo_version_name, demo_version_guid, client_name, game_directory`.
We use `map_name`. May throw — we wrap it in try.

### parse_grenades

All coordinates of all grenades + thrower info. `entity_id` distinguishes
grenades of the same type from the same player.

### parse_weapons

WEAPONS ON THE GROUND (dropped, off corpses, not picked up). Weapons in hands
are NOT here — use `inventory` / `active_weapon_name` in parse_ticks.
`m_nDropTick` is a server tick (demo tick + constant); only compare it with
other m_n\*Tick values.

## 3. Player props (parse_ticks / player= in events)

### Position / movement / view — used in core.py (POS)

- `X, Y, Z` (m_vec + m_cell), `pitch, yaw` (m_angEyeAngles[0]/[1])
- `velocity, velocity_X, velocity_Y, velocity_Z` — ⚠️ COMPUTED BY THE PARSER
  FROM POSITIONS, NOT the real server velocity. Semantics (sources,
  second_pass/collect_data.rs::velocity_from_history):
  `velocity_X[i] = (pos[i-1] - pos[i-2]) * 64`, where i-1/i-2 are the TWO
  PREVIOUS REQUESTED rows of the same player (lagged by one row!), and the
  64 multiplier always assumes "1 tick between rows". With sparse ticks=
  (only shot ticks, gaps of 5–5000 ticks) real speed is inflated N-fold
  (N = real gap), and spawn/respawn teleports yield 30 000–47 000 u/s.
  CONCLUSION: with sparse ticks= NEVER use them; compute speed ourselves:
  parse shot ticks AND t-1, speed = |Δpos|\*64 (core.\_add_speed).
  Edge case: > 600 u/s = artifact → NaN.
- `max_speed` (m_flMaxspeed), `duck_amount` (m_flDuckAmount), `ducked`,
  `ducking`, `in_crouch`, `jump_velo`, `fall_velo`, `is_airborne`
  (m_hGroundEntity), `is_strafing` (m_bStrafing)

### Survival / team / connection

- `health, armor_value (m_ArmorValue), is_alive (m_bPawnIsAlive),
life_state, team_num (m_iTeamNum), pending_team_num`
  ✅ We now request `is_alive` in core.py: we use it to filter speed (a dead
  pawn = garbage positions) and to cut the timeline at the moment of death —
  key presses and the "speed" of a dead body in the demo are NOT real
  player input.
- `player_name, player_steamid (m_steamID), user_id, entity_id, ping`

### Aim/shooting state — CANDIDATES for project improvements

- `is_scoped (m_bIsScoped)`, `is_walking (m_bIsWalking)`,
  `flash_duration (m_flFlashDuration)` — blindness in ticks!
- `aim_punch_angle` (CCSPlayerPawn.m_aimPunchAngle) — RECOIL ANGLE.
  Our angle_err() currently IGNORES recoil; can be improved: subtract/account
  for the punch angle when computing aim error.
- `velo_modifier` (m_flVelocityModifier) — slow on hit (player takes damage →
  speed drops). Useful as a "they were just shot" indicator.
- `shots_fired (m_iShotsFired)` — can be cross-checked with weapon_fire.
- `active_weapon (m_hActiveWeapon)`, `active_weapon_name` — active weapon,
  `inventory`, `inventory_as_ids` — inventory.
- `has_helmet, has_defuser, is_defusing, in_bomb_zone, in_buy_zone,
spotted (m_bSpotted), approximate_spotted_by`

### Economy/service

`balance, cash_spent_this_round, total_cash_spent, start_balance,
round_start_equip_value, current_equip_value, rank, comp_wins, score, mvps,
crosshair_code, player_color, fov, viewmodel_*`

## 4. Buttons (m_nButtonDownMaskPrev) — IMPORTANT: A BUG WE FOUND IN OUR core.py

### Named props (README):

`FORWARD, LEFT, RIGHT, BACK, FIRE, RIGHTCLICK, RELOAD, INSPECT, USE, ZOOM,
SCOREBOARD, WALK` (+ raw `buttons` = the u64 bitmask itself).
**DUCK and JUMP are NOT named props!**

### Bitmask bit map of `buttons` (from examples/buttons_bitfield/main.py):

```
IN_ATTACK      = 1 << 0    # FIRE
IN_JUMP        = 1 << 1    # JUMP — no named prop
IN_DUCK        = 1 << 2    # DUCK (crouch) — no named prop
IN_FORWARD     = 1 << 3    # FORWARD
IN_BACK        = 1 << 4    # BACK
IN_USE         = 1 << 5
IN_CANCEL      = 1 << 6
IN_TURNLEFT    = 1 << 7    # (turn, not strafe)
IN_TURNRIGHT   = 1 << 8
IN_MOVELEFT    = 1 << 9    # LEFT (strafe A)
IN_MOVERIGHT   = 1 << 10   # RIGHT (strafe D)
IN_ATTACK2     = 1 << 11   # RIGHTCLICK
IN_RELOAD      = 1 << 13   # RELOAD
IN_ALT1        = 1 << 14
IN_ALT2        = 1 << 15
IN_SPEED       = 1 << 16   # (shift-sprint)
IN_WALK        = 1 << 17   # WALK (shift-walk)
IN_ZOOM        = 1 << 18
IN_WEAPON1     = 1 << 19
IN_WEAPON2     = 1 << 20
IN_BULLRUSH    = 1 << 21
IN_GRENADE1    = 1 << 22
IN_GRENADE2    = 1 << 23
IN_ATTACK3     = 1 << 24
IN_SCORE       = 1 << 33
IN_INSPECT     = 1 << 35
```

### CONSEQUENCE FOR OUR PROJECT (the bug!)

Our core.BUTTONS = [..., "DUCK", "JUMP", ...] asks parse_ticks for named
props that don't exist. If the parser throws on an unknown prop — the whole
POS + BUTTONS call fails, the fallback to POS-without-buttons kicks in and we
lose ALL buttons (FIRE and WASD too).

**Fix** (applied in core.py): request only documented named props + raw
`buttons`, and decode DUCK/JUMP from the bitmask ourselves:
`df["DUCK"] = buttons & (1<<2) != 0`, `df["JUMP"] = buttons & (1<<1) != 0`.
Bitmask values are ≤ 2^35, so float64 conversion (the SteamID problem) is
harmless for the bitmask.

In JS the bitmask comes as a STRING (no u64) — keep in mind when porting.

## 5. Game state (for other= in events)

- `total_rounds_played (m_totalRoundsPlayed)` — we use it (0-based).
- `is_freeze_period, is_warmup_period` — FILTER: warmup/freeze shots can be
  excluded (we currently do NOT filter — the user may have "extra" shots in
  stats due to warmup!).
- `is_bomb_planted, round_start_time, round_win_status, round_win_reason,
game_phase, is_match_started, is_matchmaking, match_making_mode`
- Team props: `team_rounds_total, team_name, team_score_first_half,
team_score_second_half, team_clan_name`

## 6. Weapons / usercommands / aggregates

### Weapons (player props)

`active_weapon_name, active_weapon_ammo (m_iClip1), total_ammo_left,
weapon_mode, accuracy_penalty (m_fAccuracyPenalty!), i_recoil_idx,
is_in_reload, zoom_lvl, needs_bolt_action, iron_sight_mode, weapon_float,
weapon_paint_seed, weapon_stickers` etc.
`accuracy_penalty` — another candidate: the real accumulated accuracy
penalty.

### usercommands (a subset is available in parse_ticks!)

`usercmd_viewangle_x/y/z, usercmd_buttonstate_1/2/3,
usercmd_forward_move, usercmd_left_move, usercmd_impulse,
usercmd_mouse_dx, usercmd_mouse_dy (!), usercmd_weapon_select,
usercmd_input_history, usercmd_subtick_moves, usercmd_consumed_server_angle_changes`

`usercmd_mouse_dx/dy` — REAL MOUSE MOVEMENT per tick.
The app guide says "mouse movement isn't in demos" — that's NOT entirely
true: mouse deltas are available via usercmd. Top candidate for testing and
adding to the timeline (reaction time, mouse-flick size).

### Aggregates (updated once per round)

`kills_total, deaths_total, assists_total, alive_time_total,
headshot_kills_total, ace/4k/3k_rounds_total, damage_total,
utility_damage_total, enemies_flashed_total, equipment_value_total,
money_saved_total, kill_reward_total, cash_earned_total`
— can be used to cross-check our stats against the "official" ones (data_checks!).

## 7. Events used by our core.py

- `weapon_fire`: columns `tick, user_steamid, user_name, weapon`.
  Our load_demo: is_gun filter, norm_weapon, fix_ids(user_steamid).
- `player_hurt`: `attacker_steamid, user_steamid, weapon, hitgroup (int),
dmg_health, dmg_armor...`. hitgroup: 1=head, 2=chest, 3=stomach, 4=left_arm,
  5=right_arm, 6=left_leg, 7=right_leg, 8=neck (our HITGROUPS map).
- `player_death`: `attacker_steamid, user_steamid, weapon, headshot,
distance?, noscope, thrusmoke, attackerblind, penetrated, assistedflash...`
  We take fields via getattr with fallbacks — some fields are missing in
  different demo versions.

## 8. Examples (examples/) — summary of what was read

- **analyse_aim/main.py**: for each player_hurt takes the attacker's
  pitch/yaw for 300 ticks (~5 s) before the event:
  `df[(df["tick"].between(t-300, t)) & (df["steamid"] == int(attacker))]`.
  Confirms: steamid in parse_ticks is compared as int; the attacker can be
  None (C4 damage etc.).
- **buttons_bitfield/main.py**: the bit map (section 4) +
  `extract_buttons(buttons)` decoder iterating KEY_MAPPING.
- **kills_per_round/main.py**: `parse_event("player_death",
player=["last_place_name", "team_name"], other=["total_rounds_played",
"is_warmup_period"])` → teamkill filter via `attacker_team_name !=
user_team_name` and warmup filter via `is_warmup_period == False`.
  A TEMPLATE for our warmup/teamkill filter "in one query".
- **get_all_events/main.py**: `list_game_events()` — event names;
  `parse_events(["all"])` — ALL events in one call (the author calls it a
  "cursed solution" — works, but unofficial).
- **scoreboard/main.py**: `max_tick = parse_event("round_end")["tick"].max()`
  → `parse_ticks(["kills_total", "deaths_total", ...], ticks=[max_tick])` —
  final scoreboard for cross-checking in data_checks.
- **round_time/main.py**: `parse_event("player_death", other=["game_time",
"round_start_time"])` → death time in round = game_time −
  round_start_time. A ready-made way to get "round second" for each duel
  (early/late peeks).
- **kda_per_zone/main.py**: `player=["last_place_name"]` → map zones
  (attacker_last_place_name / user_last_place_name) — K/D by map zone.
- **find_bomb_site/main.py**: bomb_planted + player=["last_place_name"] →
  "BombsiteA"/"BombsiteB" (the site field gives an int).
- **chicken_kills/main.py**: other_death event, othertype == "chicken".
- **util_dmg/main.py**: HE damage via weapon == "hegrenade", molotov —
  "molotov" | "inferno" (the molotov fire itself = a separate entity).
- Not read (conversion errors/limit): 1vX, equipment_value_freeze_end,
  crosshair_codes, voice_to_wav, WASM. Irrelevant to our project.

## 9. Gotchas we've already collected (NOT from the docs)

1. **SteamID float64**: polars→pandas conversion turns UInt64-with-nulls into
   float64 → SteamID64 rounding. Fix: {float: str} lookup from
   parse_player_info (core.fix_ids).
2. **players= expects int-steamid** (int(me)), not a string.
3. **parse_ticks without ticks=** reads the WHOLE demo (slow). We always pass
   a tick list.
4. **total_rounds_played is 0-based**: "round 1" = 0. We add 1 in the UI.
5. **Missing event columns** (distance, noscope etc. in old demos) — always
   getattr with a default.
6. `parse_events` returns a list of tuples — wrap in dict().
7. Buttons may be missing entirely (old demos/versions) — we have
   missing-mechanism in timeline_data.
8. **⚠️⚠️ velocity_X/Y with sparse ticks=** (details in section 3): position
   delta of two previous REQUESTED rows × 64, lagged by a row and ignoring
   the real tick gap. Real speed of 191 u/s in a spray became 1146 (×6),
   respawn teleport — 35 261 (= 551 units × 64). Fix: own speeds from
   Δpos(t-1 → t) (core.\_add_speed); we don't request velocity props at all.
   Confirmed by Rust sources + user data.
9. **⚠️⚠️ fix_ids: NEVER convert steamid via Int64 → str.**
   Rounded float values arrive (16-grid) in NON-float dtypes (object /
   nullable): to_numeric → Int64 → str "bakes in" the rounding, and only
   players with sid ≡ 0 (mod 16) survive. In a real match exactly one of ten
   players survived (76561199129512544) — he had all the "hits", the rest 0.
   Fix: map any dtype through an flookup float64 key (keys built from
   parse_player_info round the same way — always match), collisions (two sids
   in one grid cell) resolved by name, else NA.
10. **hit matching fire↔hurt**: player_hurt usually lands in the same tick
    as weapon_fire, but sometimes ±1–2. A naive merge on (tick, sid, weapon)
    or a ±window set loses hits or marks extra burst shots as hits.
    Fix: assign each hurt to the LAST shot of the same weapon with
    tick ≤ hurt_tick within 3 ticks (core.\_match_hits, HIT_WINDOW=3).
11. **events after death**: a dead player has NO weapon_fire (verified on a
    real demo: shot at tick 29288 — death at 29295). But a dead pawn's buttons
    (m_nButtonDownMaskPrev) and positions are stale garbage: we cut the
    timeline by is_alive, speed of dead rows = NaN.

## 10. How this maps onto our project (improvement candidates)

| Feature                               | Prop/API                                                | What it gives                                                                                                   | Status                                             |
| ------------------------------------- | ------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| **BUG: DUCK/JUMP**                    | raw `buttons`, bits 1<<1/1<<2                           | without the fix the fallback could kill ALL buttons in the timeline                                             | ✅ fixed                                           |
| **BUG: SteamID collision in float64** | names (`user_name`/`name`) for disambiguation           | two SteamIDs differing in trailing digits collapse into one float — old lookup lost a player                    | ✅ fixed (fix_ids + build_id_lookup)               |
| **BUG pandas: empty-df mask**         | guards in load_demo/shots_table/weapon_stats            | a boolean filter on an already-empty df loses ALL columns → KeyError on demos without player_hurt / from warmup | ✅ fixed                                           |
| Warmup filter                         | `is_warmup_period` in other= (per kills_per_round)      | cleans extra warmup shots                                                                                       | ✅ implemented (counter in debug report)           |
| Recoil in aim error                   | `aim_punch_angle` in POS in parse_ticks                 | err is now "where the bullet could go", not just pre-recoil crosshair                                           | ✅ implemented (with fallback and 3 formats)       |
| Distance risk model                   | own calculations (dist to nearest living enemy)         | "risky shots" accounting for distance                                                                           | ✅ implemented                                     |
| Debug report                          | `data_checks` + `debug_report` (JSON)                   | user sends the JSON — we can see where data/logic lies                                                          | ✅ implemented                                     |
| Stats cross-check                     | `kills_total`/`deaths_total` at the last event tick | data_checks: our stats vs game stats                          | ✅ implemented (step 5, tolerance rules in s.14)   |
| Blindness by ticks                    | `flash_duration`                                        | not just attackerblind on kills                                                                                 | ✅ implemented (step 4, at fire ticks)              |
| Mouse reaction                        | `usercmd_mouse_dx/dy`                                   | reaction time and mouse flicks                                                                                  | ✅ implemented (step 6: flicks + first-shot acc; reaction needs enemy-visibility data, not in demos - s.15) |
| **BUG: velocity with sparse ticks=**  | own Δpos(t-1→t)×64, is_alive, 600 cap                   | speed lied N-fold (N=tick gap), teleports = 35k+ u/s                                                            | ✅ fixed (confirmed by Rust sources and user data) |
| **BUG: fix_ids Int64→str**            | flookup-float key for any dtype                         | "hits" only for the player with sid divisible by 16                                                             | ✅ fixed (reproduced 1/10 → 10/10)                 |
| Hit matching with window              | \_match_hits: last shot ≤ hurt_tick ± 3 ticks           | off-by-one protection without false hits in bursts                                                              | ✅ implemented                                     |
| Is the enemy alive — exactly          | `is_alive` in parse_ticks                               | instead of reconstructing from deaths                                                                           | ✅ partial (speed + timeline cut at death)         |
| Crouch in head height                 | `duck_amount`                                           | refine angle_err                                                                                                | ✅ implemented (angle_err, duck-aware heights)     |
| Duel round second                     | `game_time` − `round_start_time` in other= (round_time) | context: early peek or late retake                                                                              | ✅ implemented (step 4: shots + duels)              |
| K/D by map zone                       | `player=["last_place_name"]` (kda_per_zone)             | "where do I die most"                                                                                           | ✅ implemented (step 4: by-zone table, duels page)  |
| Accuracy penalty                      | `accuracy_penalty`                                      | direct metric instead of heuristic                                                                              | candidate (skipped: overlaps speed/recoil)         |
| Damage slow                           | `velo_modifier`                                         | duel context                                                                                                    | ✅ implemented (step 4: slowed at fire ticks)       |

## 11. Tests

- `tests/test_core.py` — 310 checks on a synthetic demo (mocked demoparser2):
  warmup filter, teamkills, SteamID collision+names, REGRESSION of the
  "Heylaurens bug" (rounded floats in object dtype: 1/10 → 10/10), distances
  to living enemies, hit matching with tolerance window, own speeds
  (Δpos t-1→t, teleport cap, dead rows), risks, punch (3 formats + fallback),
  buttons (DUCK/JUMP from bitmask, partial rejections, is_alive), data_checks,
  debug_report, missing events.
- `tests/check_ui.py` — cross-checks t() keys in app.py ↔ i18n en/ru,
  format placeholders, presence of all core.\* and columns.

## 12. Real-demo round encoding — step 2A diagnostics (2026-11-11)

Sources: two real demos parsed with `tools/inspect_rounds.py`
(demoparser2 0.42.1, the version pinned in requirements.txt):

- `faceit_mirage.dem.zst` — FACEIT, de_mirage (17 played rounds, 13-x end)
- `competitive_mirage.dem.bz2` — Valve Competitive/Premier, de_mirage
  (16 played rounds, 13-x end)

### 12.1 Event availability (both demos, 48 events each)

- **`round_start` and `round_end` DO NOT EXIST** in CS2 demos as exposed by
  demoparser2 (`list_game_events()`), so step 2B4 cannot add them. There is
  no `winner`/`reason` field anywhere → **round winner and score are NOT
  reliably available via events; do not show them** (degrade silently).
- Round boundary events that DO exist: `round_freeze_end`,
  `round_prestart` / `round_poststart`, `round_officially_ended`,
  `cs_pre_restart`, `begin_new_match`, `round_announce_last_round_half`,
  `round_announce_match_point`, `round_announce_warmup`,
  `cs_win_panel_match`.
- `round_freeze_end` / `begin_new_match` columns: only `tick` plus whatever
  is merged via `other=` (`total_rounds_played`, `is_warmup_period`).
- Valve demo also has `rank_update`, `bullet_damage`, `player_bullet_hit`;
  FACEIT demo has `bomb_defused`, `chat_message`, `player_blind` — event
  sets differ slightly per server, never assume an event is present.

### 12.2 FACEIT: knife round (the defect we must fix)

| tick | what happens |
| --- | --- |
| 1280 | `round_freeze_end` — knife round starts. `trp=0`, **`is_warmup_period=False`** (so the current warmup filter does NOT drop it) |
| 1299+ | knife-only `weapon_fire` (knife/knife_t/knife_gut/knife_falchion) |
| 2361–2658 | 6 knife `player_death`, all `trp=0` → **today they merge into round 1 (= pistol round), polluting K/D, duels, first-shot stats** |
| 2836 | `round_announce_warmup`, `round_prestart`, `round_poststart` (post-knife warmup phase) |
| 2920 | `cs_pre_restart` |
| 2940 | **`begin_new_match`** — the live match officially starts here |
| 2940–4635 | no deaths/fires at all (warmup/freeze) |
| 4635 | `round_freeze_end` — **pistol round (live round 1)**, `trp=0` again |
| 10260+ | `trp=1,2,...,16` — rounds 2..17 |

Key facts:

- The counter never resets *during* the knife round; instead the whole match
  restarts via `begin_new_match` **after** the knife round, and `trp` starts
  from 0 again. Both the knife round and the pistol round sit at `trp=0`;
  **`trp` alone cannot tell them apart — the tick of `begin_new_match` can.**
- All 6 knife kills happen strictly before `begin_new_match` (2940).
- `is_warmup_period=False` on every event in this demo (even during the
  post-knife warmup phase there are no fire/death rows to carry it).
- A knife kill also occurs mid-match (tick 32750, `trp=4`): step 2B2 must
  filter duels by weapon, not by round.
- Confirmed on a second FACEIT demo (de_inferno, 19 rounds): same shape, plus
  a warmup round AFTER the knife round and before `begin_new_match` - its
  `round_freeze_end` IS flagged `is_warmup_period=True` (knife round is not).
  So: `excluded` = {knife_round: 1, warmup: 1} there; the old warmup filter
  alone dropped only the warmup round, never the knife round.
- MR12 checks out on real data: round 13 (`trp=12`) fired only
  glock/usp_silencer (second pistol round), round 14 = full buys.

### 12.3 Valve Competitive/Premier: no knife round

| tick | what happens |
| --- | --- |
| 66 | `begin_new_match` — essentially at demo start |
| 1441 | `round_freeze_end` — pistol round immediately (glock x91 / usp_silencer fires) |
| 6553+ | `trp=1..15` — rounds 2..16, monotonic, no resets |

No knife round, no warmup, no restarts. Everything with `tick >= 66` is live.

### 12.4 Proposed detection for step 2B

1. **Live-match start** = tick of the **last** `begin_new_match` event (use
   the last one: a mid-match restart genuinely restarts the match). If the
   event is absent, keep everything (fallback below).
2. Everything with `tick < live_start` is excluded, counted per cause for the
   UI banner: rounds with `is_warmup_period=True` → warmup; round segments
   before `live_start` that are not warmup → knife round; the rest → restart.
   Excluded round count = number of `round_freeze_end` with `tick < live_start`
   (1 for the FACEIT demo above, 0 for the Valve one).
3. Round number for live events = `total_rounds_played + 1`.
4. **Fallback without `begin_new_match`** (old/3rd-party demos): the first
   round segment (bounded by `round_freeze_end`) with zero gun fires and
   knife-only deaths is the knife round → exclude it too.
5. Halves/OT (B3) from `demo.sides` swaps; MR12 (1-12 / 13-24, pistols 1 and
   13) stays the fallback — verified correct on the FACEIT demo.
6. Duels (B2): exclude knife/grenade/world/zeus/C4 kills from `build_duels`
   (mid-match knife kills exist); K/D on Overview stays all-weapon and is
   labelled as such.

Reproduce: `python tools/inspect_rounds.py faceit.dem.zst premier.dem.bz2`.

### 12.5 Step 2B verified end-to-end on the real demos (2026-11-11)

After implementing 2B1-2B6, all three real demos were run through the full
pipeline (`load_demo` → `shots_table` → `build_duels` → `player_summary` →
`data_checks`) — 41/41 checks pass:

| demo | `excluded` | live rounds | halves | pistols |
| --- | --- | --- | --- | --- |
| faceit_mirage (de_mirage) | `{warmup: 0, knife_round: 1, restart: 0}` | 17 | 1-12 / 13-17 | 1, 13 |
| faceit2 (de_inferno) | `{warmup: 1, knife_round: 1, restart: 0}` | 19 | 1-12 / 13-19 | 1, 13 |
| competitive_mirage (Valve) | `{warmup: 0, knife_round: 0, restart: 0}` | 16 | 1-12 / 13-16 | 1, 13 |

- The knife round's 6 kills no longer leak into the pistol round; the
  mid-match knife kill (tick 32750) stays in `deaths` for the all-weapon K/D
  but produces no duel.
- Halves/pistols are derived from `sides` swaps (B3) and land exactly on the
  MR12 grid in all three demos.
- Raw `total_rounds_played` in live data is 0-based (first live round = 0,
  last = rounds-1); the UI keeps adding +1 (gotcha 9.4).

## 13. Step 4 — shot & duel context: what the real demos provide (2026-11-11)

Probed on all three real demos before implementation
(`scripts/probe_step4_candidates.py`, outside the repo). Everything below
was verified on every demo, not read from the docs.

### 13.1 last_place_name (map zones)

- `player=["last_place_name"]` on `parse_events` → `attacker_last_place_name` /
  `user_last_place_name` on deaths and hurts, `user_last_place_name` on fires;
  also available directly in `parse_ticks`. ~100% non-null everywhere
  (faceit_mirage: 131/132 death rows, 2892/2892 fire rows).
- Values are the game's named places: `BombsiteA`, `Middle`, `TSpawn`,
  `CTSpawn`, `TRamp`, `Stairs`, `Apartments`, `TopofMid`, `Catwalk`,
  `PalaceInterior`, `Underpass`, `Connector`, `Shop` ...
- player_hurt does NOT carry the kill-time flags (`attackerblind`,
  `thrusmoke`, `penetrated`, `noscope`, `distance`) — only player_death does.

### 13.2 game_time − round_start_time (round clock)

- Both come back via `other=[...]` on every event AND in `parse_ticks`;
  float32, 100% non-null on all three demos.
- **`round_start_time` is the freeze END** (round going live), not the freeze
  start: fires appear 0.28 s after it, which is impossible during freeze time.
  So `round_second = game_time − round_start_time` is play time without buy
  time. Kill round-seconds on the real demos: 7.5–110 s, median ~30 s.
- CS2 `parse_header()` has no usable `playback_ticks` (0 on all three) →
  no end-of-demo tick; `tickrate_from_header` falls back to 64 (correct for
  these demos).

### 13.3 flash_duration / velo_modifier (shooter state)

- Both are `parse_ticks` player props; float, non-null at fire ticks on all
  three demos. Sampled across every fire tick of faceit_mirage (2788 ticks,
  27880 rows): `flash_duration > 0` on 4.4% of rows (max ~5 s),
  `velo_modifier < 0.99` on 11.8% (min 0.206) — a real, frequent signal.
- `attackerblind` at kills is nearly never True (0/132, 1/147) — by the time
  a kill lands the flash is usually below the game's blind threshold, so
  **flash_duration at the fire tick is the usable signal**, not the kill flag.
- `thrusmoke` kills do exist (16/132, 10/147); `noscope` / `penetrated` /
  `distance` are native player_death fields (all three demos).

### 13.4 aggregates (kills_total ...) — the cross-check trap, CORRECTED in s.14

The step-4 probe concluded the aggregates lag our pipeline by the final
round's kills and the cross-check "stays a candidate". That conclusion
was an artifact of the probe itself (it counted `attacker_steamid == sid`
without the enemy/suicide masks and matched float steamids via
`astype(str)`). The step-5 probe (s.14) re-measured with the exact
`player_summary` counting: **no final-round lag exists** — at the last
event tick the aggregates match our numbers per player on all three
demos. The original claim is kept here only so the correction is
traceable.

### 13.5 What step 4 shipped

- `load_demo`: events requested with `player=["last_place_name"]` +
  `other=[..., "game_time", "round_start_time"]` (three-step fallback for
  old parsers); a separate `parse_ticks(["flash_duration",
  "velo_modifier"], ticks=fire ticks)` merged into the ticks table (failure →
  columns absent, never a crash — the buttons lesson).
- `shots_table`: `zone` ("" unknown), `round_sec` (NaN outside [0, 300]),
  `flashed` (> `FLASH_SHOT_S`=0.5 s left), `slowed` (`velo_modifier` <
  `SLOWED_MAX`=0.99). Flags are nullable booleans: unknown ≠ False.
- `build_duels` / `duels_for`: `round_sec` + `killer_zone`/`victim_zone` →
  `my_zone`/`opp_zone` (kill-time values from the death event).
- UI: Round s in the duels table (basic view), Zone (full view), a by-zone
  K/D table under the duels table, and flashed/slowed discipline tips on the
  shooting page (thresholds: ≥ 20 known shots and ≥ 8% flashed / ≥ 10% slowed).
- `accuracy_penalty` was probed (51% of fire ticks > 0) but NOT shipped:
  unit-less for users and overlapping with the speed/risky metrics.

## 14. Step 5 — scoreboard cross-check: what the aggregates really are (2026-11-11)

Probed on all three real demos before implementation
(`scripts/probe_scoreboard*.py`, outside the repo), every player, zero
violations of the rules below.

### 14.1 The rules (all 30 players, 3 demos)

- `kills_total` at the last event tick == our enemy kills per player,
  EXACTLY — knife, hegrenade and molotov kills included (one knife, two
  hegrenade and two molotov kills across the demos all matched).
- `deaths_total` == our raw `player_death` rows per player (victims in
  the filtered, live-only table): suicides (`weapon=world`, attacker ==
  victim) and world deaths (planted C4, attacker `NA`) are counted by
  the game and must be counted by us as rows.
- Both aggregates reset at `begin_new_match`: the pre-live knife-round
  death we drop is equally absent from the game's counters.
- The "written once per round" lag from s.13.4 does not exist: at the
  tick of the last death of a round the round's kills are already in
  (verified on rounds 14-20 of all three demos).

### 14.2 The check (`data_checks` id `scoreboard`)

- `load_demo` samples `parse_ticks(["kills_total", "deaths_total"],
  ticks=[last event tick])` into `demo.totals`; any failure → `None` →
  the check reports a skip ("ok", n=0), never a guess.
- kills rule: `game <= ours <= game + non-gun kills + teamkills` — the
  three probed demos count knife/nade/TK kills, other servers may not;
  `game > ours` (we dropped kills) is always a mismatch.
- deaths rule: `game == raw death rows` exactly.
- level: fraction of mismatching players >= 25% → anomaly, >= 5% → warn.
- `debug_report` carries `counts.scoreboard_totals` so a mismatching
  foreign demo can be diagnosed from the exported JSON.

## 15. Step 6 — usercmd mouse deltas: what they really give (2026-11-11)

Probed on all three real demos before implementation
(`scripts/probe_step6_mouse.py`, outside the repo). The app guide's claim
"mouse movement isn't in demos" is wrong for CS2: `usercmd_mouse_dx/dy`
are parse_ticks props and the demos carry them for EVERY player.

### 15.1 Availability (all 30 player-demo pairs)

- The props exist in demoparser2 0.42.1 and come back on any tick list;
  both FACEIT and Valve demos, all 10 players per demo.
- At a player's own fire ticks: non-null deltas on 100% of rows (the
  ~6-9% null rows in the full tick table belong to OTHER players).
- The 16-tick pre-fire window of every first shot: 100% coverage
  (median and p10) — usercmds are dense per tick while alive.
- Per-tick magnitude |dx|+|dy|: p50 = 0, p90 = 11-14, p99 = 85-109,
  max up to ~780 at fire ticks (3167 on one window tick) — a real,
  spiky signal: mostly still, with short violent bursts.

### 15.2 The negative result: flick size does NOT predict hit (here)

Splitting each player's first shots into terciles of pre-fire window
movement (their own distribution, so sensitivity cancels out) gives hit
rates that scatter around zero in BOTH directions (deltas -31.8..+31.8
pp across 30 player-demo pairs, buckets of 14-33 shots). The aggregate
medians are equally inconsistent (hit vs miss: 41/39, 57/35, 45/43).
With these sample sizes the "you hit worse when you flick" verdict is
noise — NOT shipped as a claim (the s.13.4 lesson: measure before
claiming). What ships is the behavioural share and raw data instead.

### 15.3 What step 6 shipped

- `ENGAGE_GAP_TICKS = 22` (~350 ms): no shot by the same sid within the
  gap → the row is the FIRST shot of an engagement. Spray follow-ups
  are recoil control, not aim — they never get a flick value.
- `FLICK_WIN_TICKS = 16` (~250 ms): `flick_move` = Σ|dx|+|dy| over the
  window before a first shot. Partial windows sum the available ticks
  (dense in practice, s.15.1); zero rows → NA, never 0.
- A THIRD separate `parse_ticks(["usercmd_mouse_dx", "usercmd_mouse_dy"])`
  in `load_demo` (the buttons lesson: any failure → `demo.mouse = None`
  → `flick_move` all NA). The rows are never merged into `td` — extra
  ticks would corrupt the Δpos speed chain.
- `shots_table`: `first_shot` (bool, always known) + `flick_move`
  (float, NA for follow-ups and unknown props).
- `player_summary.first_acc` / `weapon_stats.first_acc`: first-shot
  accuracy (crosshair placement KPI; the AWP/USP number that matters).
- UI: "First shots" metric section (first-shot accuracy with a delta vs
  follow-up accuracy, first-shot count, median pre-shot mouse movement —
  labelled sensitivity-relative), first_acc columns in the overview
  (All columns) and per-weapon tables, and `tip_swing`: fires at >= 30
  mouse-known first shots with >= 35% of them committed at >= 2x the
  player's OWN median pre-shot movement (self-calibrating bar) — a
  behavioural share, no hit-rate claim (s.15.2).
- Reaction time (the other half of the s.10 candidate) is NOT feasible:
  it needs the moment the enemy became visible, and demos expose no
  visibility/occlusion data. Documented as closed.
