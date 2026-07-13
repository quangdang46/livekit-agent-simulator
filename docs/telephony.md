# Telephony (WebRTC / inbound SIP / outbound SIP)

Portable transport modes for lk-sim. **Mode is never set in `config.yaml`** — put it in the scenario.

## Modes

| `Caller.mode` | Gemini role | Rooms |
|---|---|---|
| `webrtc_sim` (default) | Caller (WebRTC) | 1 |
| `inbound_sip` | Caller dials agent DID | 2 (Cloud hairpin) |
| `outbound_sip` | Callee answers | 2 (Cloud hairpin) |
| `agent_dials` | Callee; agent process dials | 2 |

Primary SIP path: **LiveKit Cloud hairpin** — Gemini stays WebRTC in a sim-room; LiveKit bridges SIP. No in-process RTP stack required.

## Precedence

```text
Scenario Telephony.*  >  config.yaml telephony.*  >  built-ins
```

| Field | Scenario | Config | Required for |
|---|---|---|---|
| mode | `Caller.mode` | — | all (default webrtc_sim) |
| call_to | `Telephony.call_to` | `telephony.sim_inbound_number` | outbound_sip |
| dial_in | `Telephony.dial_in` | `telephony.dial_in` | inbound_sip |
| trunk | `Telephony.sip_trunk_id` | `telephony.outbound_trunk_id` | SIP modes |
| prepare_ms | optional | `telephony.prepare_ms` (default 3000) | outbound |
| wait_until_answered | optional | default `true` | SIP dial |

## Config example (target `.agent-sim/config.yaml`)

```yaml
livekit:
  url: "wss://your-project.livekit.cloud"
  api_key: "APIxxxxxxxx"
  api_secret: "secretxxxxxxxx"
  agent_name: "your-agent-name"
  # dispatch_metadata: '{"yourProjectKey":"value"}'   # opaque

simulator:
  google_api_key: "AIzaxxxxxxxx"

# Optional — omit for WebRTC-only
telephony:
  outbound_trunk_id: "ST_xxxxxxxxxxxx"
  dial_in: "+15551234567"              # agent inbound DID
  sim_inbound_number: "+15559876543"   # Gemini callee DID
  prepare_ms: 3000
  wait_until_answered: true
```

## Scenario examples

See `templates/outbound-callee-sim.jsonl` and `templates/inbound-caller-sim.jsonl`.

## Owner setup (outside package)

1. LiveKit outbound trunk (carrier termination URI, not LiveKit host).
2. Agent inbound DID + dispatch rule (know room naming).
3. Sim/Gemini answer path for outbound tests.
4. Secrets only in gitignored target config.

## Asserts

```json
{"kind":"Assert","spec":{"sip":{"participant_present":true,"dial_answered":true,"call_status_any":["active"]}}}
```

## Design

- **Template Method** — one orchestrator pipeline.
- **Strategy** — `SimLeg` (`WebRtc` / `InboundSip` / `OutboundSip` / `AgentDials`).
- **Factory** — `sim_leg_factory(mode)`.

Package core stays target-agnostic: no product names, agent IDs, or dashboard keys in `src/`.
