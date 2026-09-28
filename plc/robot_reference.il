; =============================================================================
; Solar panel cleaning robot - reference ladder in Delta DVP instruction list (IL)
; Target: DVP-12SE11T (ISPSoft / WPLSoft). Behavior: docs/robot-operation.md,
; same state machine as app/sim/ladder.py. Tested by tests/test_ladder_il.py
; (app/sim/il.py runs this file against the simulated plant).
;
; !! ASSUMED DEVICES !! M/D below follow config/plc_tags.yaml (placeholders).
; Change them together with the tag map. Only X0-X4 and Y0-Y1 are confirmed.
;
; Pi <-> PLC (tag map)
;   M100 cmd_start      M101 cmd_stop       M102 cmd_return   M103 cmd_reset_alarm
;   D101 cycles_setpoint  D110 battery_pct  D111 pi_heartbeat
;   D10 robot_state (0 Idle, 1 Cleaning, 2 Returning, 3 Home, 4 Alarm)
;   D11 alarm_code  D12 cycle_count  D13 position_est_pct
; I/O
;   X0 limit end 1  X1 limit end 2  X2 Start/Stop button  X3 mode (OFF = Auto)
;   X4 E-stop NC (ON = normal)  Y0 drive run  Y1 direction (ON = toward X1)
; Internal (not used by the Pi)
;   M200 button edge        M201 want stop       M202 want start
;   M204 heartbeat ok       M205 battery low     M206 battery critical
;   M207 may start (>=80%)  M209 run started at end 1
;   M210 finish cycle, then home (heartbeat lost)
;   M211 arrived at limit in travel direction
;   M212 pausing at an end  M213 timing a full end-to-end leg
;   M214 go-home request    M215 100 ms tick while moving
;   M216 end reached        M217 moving state (Cleaning or Returning)
;   M220-M222 CMP result (heartbeat >, =, <)
;   M231 both limits ON     M233 alarm reset accepted
;   M240 back at start end  M241 run finished
;   M242 start accepted
;   D200 last heartbeat     D201 leg time (0.1 s)  D202 travel time (0.1 s)
;   D203 position (0.1 s from X0)  D204-D205 MUL result
;   D206-D207 travel as 32-bit     D208-D211 DDIV result
;   D212 state at scan start       D213 cycles target
;   T0 heartbeat timeout 10 s      T1 end pause 2 s   (T0-T199 = 100 ms base)
;
; Each section reads D212 (state at the start of the scan), so one scan moves
; at most one state step, like the elif chain in app/sim/ladder.py.
; =============================================================================

; ----- 0. First scan -----
LD    M1002
MOV   D111  D200        ; heartbeat baseline only; battery trusted after a change
MOV   K200  D202        ; travel time guess 20.0 s until a full leg is measured
MOV   K0    D10
MOV   K0    D11
MOV   K0    D12
MOV   K0    D201
MOV   K0    D203
ZRST  M200  M249
SET   Y1
LD    M1002
LD    X0
OR    X1
ANB
MOV   K3    D10         ; boot at an end = Home, else Idle

; ----- 1. Inputs -----
LDP   X2
OUT   M200              ; front button, rising edge
LD    X0
AND   X1
OUT   M231
LD    X0
MOV   K0    D203        ; limit = known position
LD    X1
MOV   D202  D203

LD    M1000
MUL   D203  K100  D204
MOV   D202  D206
MOV   K0    D207
LD    M1000
AND>  D202  K0
DDIV  D204  D206  D208
MOV   D208  D13         ; position % = D203 * 100 / D202
LD>   D13   K100
MOV   K100  D13

; ----- 2. Pi heartbeat watchdog -----
LD    M1000
CMP   D111  D200  M220  ; M221 ON = unchanged
LDI   M221
MOV   D111  D200
SET   M204
LD    M221
TMR   T0    K100
LD    T0
RST   M204

; ----- 3. Battery (only with a live heartbeat) -----
LD    M204
AND<  D110  K25
OUT   M205
LD    M204
AND<  D110  K20
OUT   M206
LD    M204
AND>= D110  K80
OUT   M207

; ----- 4. Stopping faults -----
LD    M231
AND   X4
MOV   K4    D10
MOV   K4    D11         ; alarm 4: both limits
LDI   X4
MOV   K4    D10
MOV   K1    D11         ; alarm 1: E-stop (wins over alarm 4)
LD    M1000
MOV   D10   D212

; ----- 5. Warnings (robot keeps going) -----
LD<>  D212  K4
ANI   M204
MOV   K5    D11         ; alarm 5: heartbeat lost
LD<>  D212  K4
AND   M204
AND   M206
MOV   K2    D11         ; alarm 2: battery critical
LD<>  D212  K4
AND   M204
ANI   M206
MOV   K0    D11

; ----- 6. Requests (Stop wins over Start) -----
LD=   D212  K1
OR=   D212  K2
OUT   M217
LD    M200
AND   M217
OR    M101
OUT   M201
LD    M200
ANI   M217
OR    M100
ANI   M101
OUT   M202
LD    Y1
AND   X1
LDI   Y1
AND   X0
ORB
OUT   M211

; ----- 7. Home -> Cleaning -----
LD=   D212  K3
AND   M202
AND   M207
OUT   M242
LD    M242
MOV   K1    D10
MOV   K0    D12
MOV   K0    D201
MOV   D101  D213
SET   M213
RST   M212
RST   M210
LD    M242
AND<  D101  K1
MOV   K1    D213        ; at least 1 cycle
LD    M242
AND   X0
SET   M209
SET   Y1                ; leave end 1 toward end 2
LD    M242
ANI   X0
RST   M209
RST   Y1                ; leave end 2 toward end 1

; ----- 8. Idle: position unknown, go to the nearest end first -----
LD=   D212  K0
LD    M202
OR    M102
ANB
SET   M214

; ----- 9. Cleaning -----
LD=   D212  K1
ANI   M204
SET   M210
LD=   D212  K1
AND   M201
MOV   K0    D10         ; Stop -> Idle
RST   M212
RST   M213
LD=   D212  K1
ANI   M201
LD    M102
OR    M205
ANB
SET   M214              ; cmd_return or battery low -> go home
LD=   D212  K1
ANI   M201
ANI   M102
ANI   M205
ANI   M212
AND   M211
OUT   M216
LD    M212
TMR   T1    K20
LD    T1
RST   M212

; ----- 10. End reached -----
LD    M216
AND   M213
AND>  D201  K0
MOV   D201  D202        ; learn end-to-end travel time
LD    M216
MOV   K0    D201
SET   M213
LD    M216
LD    X0
AND   M209
LDI   X0
ANI   M209
ORB
ANB
OUT   M240
LD    M240
INCP  D12
LD    M240
LDI   X3
AND>= D12   D213
OR    M210
ANB
OUT   M241              ; Auto: cycles done, or heartbeat lost
LD    M241
MOV   K3    D10
LD    M216
ANI   M241
SET   M212
LD    M216
ANI   M241
AND   X1
RST   Y1                ; reverse after the pause
LD    M216
ANI   M241
AND   X0
SET   Y1

; ----- 11. Go home (from Idle or Cleaning) -----
LD    M214
AND<  D13   K50
RST   Y1
LD    M214
AND>= D13   K50
SET   Y1
LD    M214
MOV   K2    D10
RST   M212
RST   M213
RST   M214

; ----- 12. Returning -----
LD=   D212  K2
AND   M201
MOV   K0    D10
LD=   D212  K2
ANI   M201
AND   M211
MOV   K3    D10

; ----- 13. Alarm reset (E-stop released, not both limits) -----
LD=   D212  K4
AND   M103
AND   X4
ANI   M231
OUT   M233
LD    M233
MOV   K0    D11
MOV   K0    D10
LD    M233
LD    X0
OR    X1
ANB
MOV   K3    D10

; ----- 14. Outputs -----
LD=   D10   K1
OR=   D10   K2
ANI   M212
AND   X4
OUT   Y0
LDP   M1012
AND   Y0
OUT   M215
LD    M215
AND   Y1
AND<  D203  D202
INC   D203
LD    M215
ANI   Y1
AND>  D203  K0
DEC   D203
LD    M215
AND   M213
INC   D201

; ----- 15. Command bits are requests: clear after this scan -----
LD    M1000
ZRST  M100  M103
END
