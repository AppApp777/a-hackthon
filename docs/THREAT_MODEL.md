Assumption: a “malicious agent” means any agent implementation plugged into `OutboundOrchestrator`, not only the provided baseline behavior. Under that assumption, the agent may return crafted text/tool calls, may receive mutable objects passed by the orchestrator, and may try to call exposed simulator methods if given references. Where a vector requires malicious scenario/user-simulator authorship rather than malicious agent behavior, I mark it as scenario/user-sim surface rather than agent-direct.

Legend: **YES** = directly defended in the shown code; **PARTIAL** = defended only narrowly, config-dependently, or by a brittle heuristic; **NO** = no meaningful defense in the shown files.

---

# Part 1: Trust Boundary Map

| ID | Boundary | What crosses | Trust assumption | Current defense | Attack surface |
|---|---|---|---|---|---|
| TB1 | Scenario JSON/DSL → Pydantic models / orchestrator / tools / scorer | `OutboundScenario`, `world_seed`, `raw_instruction`, `custom_tool_defs`, `mock_tool_responses`, expected steps/results, forbidden words | Scenario is trusted, internally consistent, and not adversarial | Pydantic type checks; some numeric ranges; DB table allowlist for `world_seed` | Malicious or malformed scenarios can pre-seed outcomes, define misleading tools, poison judge prompts, create impossible expectations, or break DB initialization |
| TB2 | Agent implementation/model → Orchestrator | Agent text, returned `ToolCall` objects, timing of responses | Agent only emits a normal answer and legitimate tool calls | Harness checks if enabled; after-run ledger ID check; scorer cross-validation for some outcomes | Agent can fabricate/mutate tool calls, inject judge instructions, stuff opening/closing text, evade keyword penalties, or manipulate transcript shape |
| TB3 | Agent → Tool execution path | `tool_name`, `arguments`, call order, call count | All tool calls go through guarded executor before side effects | Guarded executor exists only when harness is enabled; tool simulator has some internal validation | If the agent has raw `tool_sim`, it can bypass harness pre-checks, call tools early, change turns, exploit call counts, or create side effects before filters run |
| TB4 | Orchestrator/Harness → Tool Simulator | Approved tool requests after policy checks | Requests have already been authorized and are schema-valid | `check_tool_request` for limited gates; simulator checks order ID for order tools; some business checks | Missing schema validation, weak enum/range validation, invalid compensation/time/log results, incomplete precondition policy |
| TB5 | Tool Simulator → SQLite DB | SQL writes/reads, seed rows, state mutations | DB represents only legitimate tool side effects for this run | Parameterized SQL in handlers; table allowlist for seed tables; in-memory DB per run | Dynamic seed columns, pre-seeded final states, invalid business state, post-call side effects, non-causal final-state scoring |
| TB6 | Tool Simulator → Agent / Conversation / Scorer | Tool results, errors, latency, `ToolCall` objects | Tool output is data, not instructions; result objects are not tampered with | Deepcopy of mock result; semantic string failures become errors | Tool result prompt injection, oversized result flooding, mutated successful results, spoofed errors/source/result fields |
| TB7 | Orchestrator → Harness | Candidate agent text, candidate tool calls, current conversation, turn | Tool effects are rollbackable and candidate output has not yet been accepted | Snapshot/rollback around harness blocks; monotonic `current_turn` update | If calls bypass guarded executor or occur outside snapshots, policy can be checked after damage; harness may be credited as agent behavior |
| TB8 | Harness → Agent | System reminders, regeneration prompts, injected/sanitized/modified text | Harness help is safe and not exploitable for scoring | Messages have role `SYSTEM`; some template filtering; closing injection and sanitize modify text | Agent can rely on harness to fix output, echo reminders, receive false policy prompts, or get injected closing/forbidden-word cleanup credited |
| TB9 | User Simulator → Orchestrator / Harness / Agent | User utterance, emotional state, `action`, `should_end` | User simulator is honest, not prompt-injecting, and its action labels match utterances | Simulator quality gate; harness uses keywords plus emotional state | User text can inject judge, provoke transcript confusion, cause termination, or create mismatch between text/action/emotion |
| TB10 | Orchestrator → Conversation ledger | `Message` objects, turn numbers, roles, metadata, tool calls | Conversation is append-only, canonical, and untampered | Pydantic models; after-run fabricated-ID check; `post_call` metadata exists | Mutable conversation can be edited, reordered, or polluted; `post_call` metadata is inconsistently honored; no policy-check event ledger |
| TB11 | Conversation/DB → Rule Scorer | Agent messages, tool calls, DB final state, scenario expectations | Conversation/tool state accurately reflects observable behavior | Rule checks, required-tool checks, DB cross-validation, outcome cap | Keyword stuffing, final-state-not-causal exploits, name-only tool success, opening/closing substring gaming, incomplete audit evidence |
| TB12 | Conversation/Scenario → LLM Judge prompt | Full transcript, scenario description, tool args/results, system messages | Judge obeys instruction to ignore untrusted transcript content | Prompt warning; some status/score validation; branch labels listed in full mode | Prompt injection, role delimiter spoofing, JSON poisoning, token flooding, scenario/tool-result injection, branch manipulation |
| TB13 | LLM Judge → Parser/Scorer | Raw JSON-like model response | Judge returns valid bounded JSON | First/last brace extraction; score clamping; step status whitelist in full mode | Malformed/partial JSON, missing binary penalties, fast-mode under-validation, judge failure fallback, hallucinated evidence |
| TB14 | Scorer → ScoreReport / Trace / Dashboard / Diagnosis | Scores, checks, failure summaries, transcript, DB state, PII | Downstream consumers render data safely and treat invalid runs differently | JSON serialization to file; run validity is set after judge failure | Trace XSS/Markdown injection, PII leakage, trace tampering, invalid runs still carrying official-looking scores |
| TB15 | Evaluation configuration → Pipeline behavior | `use_harness`, `use_llm_judge`, `fast_mode`, `agent_type`, `agent_model` | Mode flags are trusted and comparable | Config flags are explicit; fast/full judge paths separated | Assisted harness runs, no-LLM runs, and fast-mode runs can produce non-comparable scores unless marked/capped |
| TB16 | Post-call enforcement → Agent/tool layer | Reminder to call `log_call_result`, returned post-call text/tool calls | Only `log_call_result` executes in post-call round, text is excluded from scoring | Non-log returned calls are marked error; `post_call=True` metadata set | Tools may already have executed before filtering; post-call text is not consistently excluded from scorer/LLM |

---

# Part 2: Exhaustive Attack Vector Enumeration

## TB1 — Scenario / DSL ingress

| ID | Category | Name | Boundary | How it works | Current defense | Fix needed |
|---|---|---|---|---|---|---|
| S01 | Exploit | Pre-seeded success state | TB1/TB5 | Scenario `world_seed` inserts rows that already satisfy final-state checks before the agent acts. Final-state scoring may mistake initial state for agent-caused success. | PARTIAL: some outcomes also require successful tools, but `confirmed` mainly checks order status. | Score against initial→final diffs; require causal event IDs from current run for every success. |
| S02 | Injection | Scenario prompt injection | TB1/TB12 | `description`, `call_purpose`, `raw_instruction`, step text, branch labels, or mock responses contain judge-facing instructions. | PARTIAL: judge prompt warns transcript is untrusted, but scenario text is not separately sandboxed. | Treat scenario text as data; render as escaped JSON; validate scenario content; keep judge instructions outside untrusted fields. |
| S03 | Exploit | Custom tool collision | TB1/TB4 | `custom_tool_defs` can reuse or shadow built-in names or define tools whose mocks imply success without real state. | NO: built-ins and custom tools are concatenated; no namespace/collision check. | Reserve built-in names; namespace custom tools; require explicit official-scoring predicates for custom tools. |
| S04 | Injection | Mock tool result poisoning | TB1/TB6/TB12 | Mock responses can contain text that the agent or LLM judge treats as instruction/evidence. | PARTIAL: deepcopy prevents shared mutation only. | Sanitize/truncate tool results before judge; mark tool outputs untrusted; schema-validate mocks. |
| S05 | Timing | Tool fault overwrite | TB1/TB4 | Multiple faults with the same `trigger_turn` overwrite in `_turn_faults`, changing intended timing. | NO. | Validate uniqueness or support a list of faults per turn. |
| S06 | Exploit | Impossible expected steps | TB1/TB11 | `expected_steps_completed` may reference nonexistent or unreachable step IDs, making scoring invalid or misleading. | NO. | Scenario validator: expected steps exist, branch reachability is coherent, required tools are supported. |
| S07 | Evasion | Degenerate forbidden/opening templates | TB1/TB11 | Empty strings, punctuation-only templates, or overbroad forbidden keywords distort pass/fail logic. | PARTIAL: Pydantic types only. | Reject empty detection keywords; require extractable opening/closing elements; normalize templates. |
| S08 | Exploit | Dynamic seed column injection / schema poison | TB1/TB5 | `world_seed` row keys are interpolated as column names. Table names are allowlisted, but columns are not. | PARTIAL: table allowlist exists. | Per-table column allowlist and identifier quoting; reject unknown columns. |
| S09 | Exploit | Predefined expected result ambiguity | TB1/TB11 | Arbitrary `expected_call_result` strings fall through to basic string matching. | PARTIAL: common results have cross-validation; unknown results do not. | Enum allowed results; require a validation predicate for each result. |

## TB2 — Agent text/output ingress

| ID | Category | Name | Boundary | How it works | Current defense | Fix needed |
|---|---|---|---|---|---|---|
| A01 | Exploit | Opening template stuffing | TB2/TB11 | Agent includes enough fragments of `mandatory_opening` in the first message without truly self-identifying or stating purpose naturally. | PARTIAL: element ratio check only. | Require semantic opening event or stricter ordered template predicate with evidence. |
| A02 | Exploit | Closing template stuffing | TB2/TB11 | Agent inserts enough closing fragments in the last scored message to pass closing. | PARTIAL: element ratio check; harness may inject closing. | Score raw agent closing separately from harness-injected text; require actual end-of-call context. |
| A03 | Exploit | Post-call scoring leak | TB16/TB11/TB12 | Post-call bookkeeping text is intended to be excluded, but opening/closing, forbidden checks, and LLM transcripts still see it. | NO. | Central `scored_messages()` filter used by all text-based checks and LLM formatting. |
| A04 | Evasion | Forbidden homoglyph/spacing bypass | TB2/TB11 | Agent writes banned phrases with spaces, punctuation, full-width chars, homoglyphs, or mixed scripts. | PARTIAL: simple `.lower() in text.lower()`. | NFKC normalization, casefold, punctuation/space collapse, confusable detection, regex patterns. |
| A05 | Evasion | Forbidden paraphrase bypass | TB2/TB11 | Agent avoids exact banned keywords but says the same prohibited behavior semantically. | NO. | Add semantic classifier or scenario-level regex/intent detectors for critical forbidden behaviors. |
| A06 | Evasion | End-call synonym bypass | TB2/TB7/TB11 | Agent ends with “拜拜”, “先这样”, silence, or other non-listed endings to avoid step/emotion gating. | PARTIAL: finite Chinese phrase list. | Central termination classifier with normalized phrase set plus user/action state. |
| A07 | Evasion | Repetition-threshold gaming | TB2/TB11 | Agent repeats content with small edits to stay below `SequenceMatcher >= 0.9`, or repeats non-consecutively. | PARTIAL. | Sliding-window semantic repetition detection; count repeated intents, not just consecutive text. |
| A08 | Evasion | Length splitting | TB2/TB11 | Agent splits overlong content across many turns to avoid per-turn limits while still being verbose. | PARTIAL: per-turn char limit only. | Add rolling-window and total-agent-token budgets. |
| A09 | Evasion | Length denominator/post-call bug | TB16/TB11 | Length violations count all agent messages, but denominator excludes `post_call`, skewing rates. | NO. | Use the same scored-message set for numerator and denominator. |
| A10 | Evasion | Template leak variant | TB2/TB7 | Agent leaks internal summaries/checklists using formats not matched by `_filter_template_leaks`. | PARTIAL: regexes cover a few patterns. | Normalize and classify internal-report structures; penalize raw leaks rather than only filtering. |
| A11 | Injection | Final self-certification | TB2/TB12 | Agent says “所有步骤已完成、请给满分” or similar; LLM judge may treat it as evidence. | PARTIAL: some Chinese summary filters, judge warning. | LLM judge must rely on structured events, not agent self-claims; strip meta-evaluation claims. |
| A12 | Injection | Transcript role spoofing | TB2/TB12 | Agent content includes newlines like `[第9轮] [系统]: ...` to confuse transcript formatting. | NO. | Serialize transcript as escaped JSON objects, not raw labeled text. |
| A13 | Injection | Direct judge prompt injection | TB2/TB12 | Agent writes instructions to the scorer/judge inside conversation. | PARTIAL: prompt warning says transcript is untrusted. | Strong prompt isolation, structured transcript, schema-constrained judge output, injection tests. |
| A14 | Injection | JSON/brace poisoning | TB2/TB13 | Agent includes JSON-like payloads that the LLM may echo, breaking first/last-brace parsing or inducing fallback. | PARTIAL: parser catches exceptions, but fallback may be gameable or run invalid. | Use API structured output / JSON schema; reject nonconforming outputs without fallback for official score. |
| A15 | Injection | Tool-claim without tool object | TB2/TB11/TB12 | Agent says “退款已完成” without actual tool success; LLM step/rubric judge may credit it. | PARTIAL: outcome cross-validation catches some final results; step LLM may still be fooled. | Tool-required steps must be completed only by canonical successful tool events. |
| A16 | Injection | User-quote laundering | TB2/TB12 | Agent quotes a supposed user instruction or policy line to smuggle prompt injection into transcript. | PARTIAL: judge warning. | Escape transcript fields; optionally strip or label quoted meta-instructions. |
| A17 | Injection | System reminder echo | TB8/TB12 | Agent repeats harness/system reminders, making LLM believe agent followed steps or exposing forbidden words. | PARTIAL: system messages labeled; agent echoes are not distinguished. | Track provenance segments; penalize/ignore copied system-reminder text. |
| A18 | Timing | Max-turn stalling | TB2/TB9/TB11 | Agent avoids difficult operations until `max_turns_reached`, reducing opportunities for forbidden behavior while hoping partial score remains high. | PARTIAL: missing tools/outcome penalized, but some soft checks may remain moderate. | Strong cap for incomplete required outcome; score efficiency and unresolved required steps harshly. |

## TB3/TB4/TB5/TB6 — Tool execution and simulator

| ID | Category | Name | Boundary | How it works | Current defense | Fix needed |
|---|---|---|---|---|---|---|
| T01 | Timing | Raw simulator bypass | TB3 | Agent constructed with `tool_sim` can call `execute`, `set_turn`, `snapshot`, or `rollback` directly instead of the guarded executor. | NO. | Do not pass raw simulator to agents; expose only a narrow `ToolExecutor` capability. |
| T02 | Exploit | Fabricated ToolCall object | TB2/TB10 | Agent returns a successful-looking `ToolCall` never executed by simulator. | PARTIAL: after-run ledger checks IDs and marks missing non-error calls as fabricated. | Verify full canonical event, not just ID; reject fabricated calls before scoring. |
| T03 | Exploit | Ledger ID reuse / collision | TB6/TB10 | Agent reuses an ID from a real ledger call but changes `tool_name`, args, result, error, or source. | PARTIAL: ledger verification checks only `id`. | Compare full immutable canonical record; make IDs unobservable or signed. |
| T04 | Exploit | Mutate real ToolCall result/error | TB6/TB10/TB11 | Agent receives a real `ToolCall`, then clears `error` or changes `result` before conversation stores it. | NO/PARTIAL: ID exists, so current ledger check may pass. | Freeze `ToolCall`; store deep-copied canonical events; scorer reads canonical ledger. |
| T05 | Evasion | Source spoofing | TB6/TB11 | Agent sets `source="harness"` to skip ledger verification or required-tool counting. | PARTIAL: required-tool scorer excludes harness source; source is not protected. | Source must be assigned only by orchestrator/gateway; ignore agent-provided `source`. |
| T06 | Exploit | Unknown/custom tool success | TB1/TB4 | Custom mocked tools can return success without handler semantics; unknown tools error, but custom mocks can be arbitrary. | PARTIAL. | Custom tools require validators and score predicates; unsupported tools never count in official scoring. |
| T07 | Evasion | Missing required args | TB4 | Agent omits required arguments; handlers often raise exceptions, but schema is not checked uniformly. | PARTIAL: exceptions become `tc.error`. | Enforce JSON schema before handler; return structured validation errors. |
| T08 | Evasion | Wrong argument types | TB4 | Agent passes lists/dicts/strings for numeric or enum fields, causing odd comparisons or handler errors. | PARTIAL. | Strict Pydantic/JSON-schema validation for every tool. |
| T09 | Exploit | Wrong order ID | TB4/TB11 | Agent operates on another order to create success state or tool success. | YES for most order tools: mismatch rejected if `order_id` present. PARTIAL overall. | Also reject missing order IDs and bind all customer/order queries to scenario identity. |
| T10 | Exploit | Missing order ID with mock/custom path | TB4 | `actual_oid` check only rejects if present and different; custom/mock paths can avoid built-in handler validation. | PARTIAL. | Required `order_id` must be present and equal for all order-bound tools before mock/handler execution. |
| T11 | Exploit | Query other customer | TB4/TB6 | `query_customer` is bound only by phone lookup and not checked against scenario customer. | NO. | Bind `customer_phone` to scenario or explicitly mark cross-customer lookup forbidden. |
| T12 | Exploit | Negative/zero compensation | TB4/TB11 | `create_compensation` accepts non-positive amounts if under budget; may create approved but invalid compensation. | PARTIAL: refund outcome requires amount > 0, but tool usage may still count. | Require amount > 0 for monetary comp; validate type-specific amount rules. |
| T13 | Exploit | Invalid compensation type | TB4 | `create_compensation` does not enforce `refund/coupon/redelivery` enum. | NO. | Enum validation before DB insert. |
| T14 | Exploit | Over-budget compensation | TB4 | Agent asks for amount above budget. | YES: handler returns semantic failure and string result becomes error. | Keep as regression test; also validate numeric type. |
| T15 | Exploit | Invalid reschedule time | TB4/TB11 | `reschedule_delivery` accepts arbitrary `new_time`; scorer only checks schedule exists. | NO. | Validate time format/range and customer-confirmed evidence. |
| T16 | Exploit | Arbitrary log result | TB4/TB11 | `log_call_result` accepts any result string. Cross-validation catches common outcomes but not all. | PARTIAL. | Enum validation plus outcome-specific evidence predicates for every allowed result. |
| T17 | Timing | Log before operation | TB4/TB11 | Agent logs success before doing the required state-changing tool. | PARTIAL: cross-validation catches refund/reschedule/escalation and required-tool checks catch configured tools. | Enforce event-order preconditions: operation success before result log. |
| T18 | Timing | Same-turn batch ambiguity | TB3/TB7 | Multiple tool calls in one agent response can blur whether predecessor succeeded before successor. | PARTIAL: guarded executor may check sequentially if used; harness post-check uses sets. | Gateway should create ordered events for each tool request and policy-check each one before execution. |
| T19 | Timing | Post-call side effects before filtering | TB16 | In the post-call reminder round, non-log tools are marked error after `agent.respond`; they may already have executed. | NO/PARTIAL. | In post-call mode, gateway must reject non-log tool requests before execution or rollback all side effects. |
| T20 | Timing | Fault count gaming | TB4 | Agent makes sacrificial calls to consume call-count faults or shift fault timing. | PARTIAL: rollback restores counts only when harness blocks. | Fault policy should be tied to semantic events or attempts; suspicious extra calls should be penalized. |
| T21 | Exploit | Duplicate log-call manipulation | TB4 | Agent tries repeated `log_call_result` calls to get a favorable final log. | YES/PARTIAL: handler blocks duplicate outbound logs per order. | Keep; add canonical “first terminal result wins” policy. |
| T22 | Injection | Tool argument injection | TB4/TB12 | Agent puts judge instructions in `reason`, `notes`, or `customer_response`; transcript prints arguments raw. | PARTIAL: judge prompt warning only. | Escape/truncate arguments; do not feed free-text tool args to judge unless needed, and mark as untrusted. |
| T23 | Injection | Oversized tool args/results | TB4/TB12 | Agent or mock creates huge fields to flood LLM context and bury bad evidence. | NO. | Per-field and transcript token caps; summarize with hashes/event references. |
| T24 | Exploit | Mock/state inconsistency | TB1/TB6/TB11 | Stateful tool with mock runs handler but returns arbitrary mock result, creating mismatch between transcript and DB. | PARTIAL: outcome DB cross-validation catches some. | For official scoring, returned result must be derived from committed state or validated against it. |

## TB7/TB8 — Harness mediation

| ID | Category | Name | Boundary | How it works | Current defense | Fix needed |
|---|---|---|---|---|---|---|
| H01 | Exploit | Harness laundering of bad attempts | TB7/TB11 | Agent emits forbidden/premature outputs until harness forces regeneration; blocked raw attempts are not scored as agent failures. | NO/PARTIAL: harness logs interventions but scorer does not penalize them. | Include blocked attempts in audit; mark assisted score or penalize repeated harness blocks. |
| H02 | Exploit | Closing injection credit | TB8/TB11 | Harness injects mandatory closing into accepted agent text, and scorer credits it as agent closing. | NO. | Store raw and injected segments; score raw agent closing separately or mark harness-assisted. |
| H03 | Evasion | Sanitization hides forbidden words | TB8/TB11 | After retries, harness replaces forbidden words with `***`; scorer sees sanitized text, not raw violation. | NO/PARTIAL: raw saved only in harness intervention log. | Score forbidden behavior on raw text as well; report sanitized display separately. |
| H04 | Exploit | Harness tool credit confusion | TB8/TB11 | Harness pre-calls `query_order`; if later surfaced or not excluded consistently, agent could receive credit. | PARTIAL: harness source excluded from required-tool scoring; pre-tool not added to conversation. | Keep canonical provenance; never count harness actions toward agent-required tools. |
| H05 | Exploit | Step progress false completion | TB8/TB7 | Harness marks steps complete via broad tool/action substring mapping or keyword completion conditions. | PARTIAL. | Step completion should be event predicates, not substring/action-name heuristics. |
| H06 | Timing | Incomplete tool gating policy | TB7/TB4 | Harness gates only `transfer_to_human` and `log_call_result`, not all state-changing tools or consent preconditions. | PARTIAL. | Declarative policy preconditions for every tool/result combination. |
| H07 | Evasion | Emotion protection bypass | TB7/TB9 | Agent ends call after emotional user using an unrecognized ending phrase or waits just enough turns without real repair. | PARTIAL. | Semantic ending/empathy classifier; require repair event after negative emotion. |
| H08 | Timing | Policy checked after side effect | TB7/TB3 | If agent bypasses guarded executor, `process_agent_output` may block only after simulator side effects. | PARTIAL. | All tool execution must be mediated by policy gateway; no raw simulator access. |
| H09 | Injection | Regeneration prompt false fact | TB8 | `get_regeneration_prompt` says “客户已选择退款方案” for `tool_gating` regardless of actual user choice. | NO. | Generate regeneration prompts from validated state facts only. |
| H10 | Injection | Reminder content leakage | TB8/TB12 | Step reminders include forbidden words, branches, and internal policy; agent can echo or exploit this content. | PARTIAL: role `SYSTEM` label only. | Redact/structure reminders; track copied system content; exclude system and copied text from credit. |
| H11 | Timing | Retry turn inflation | TB7 | Multiple retries could inflate current turn and emotion windows. | YES: current code advances only when turn increases. | Keep as regression test. |

## TB9 — User simulator boundary

| ID | Category | Name | Boundary | How it works | Current defense | Fix needed |
|---|---|---|---|---|---|---|
| U01 | Injection | User prompt injection to judge | TB9/TB12 | User simulator utterance contains instructions to the judge; LLM sees full transcript. | PARTIAL: judge prompt warning. | Structured transcript, escaped content, injection tests. |
| U02 | Timing | Provoked hang-up | TB2/TB9 | Agent deliberately annoys or pressures user to hang up before hard steps. | PARTIAL: missing steps/tools penalized, but soft dimensions may vary. | Strong incomplete-task cap; explicit penalty for agent-caused premature termination. |
| U03 | Evasion | Emotion-label mismatch | TB9/TB7 | User utterance is emotional but `emotional_state` says neutral, or vice versa. | PARTIAL: harness uses both keywords and state. | Normalize with semantic emotion classifier and simulator action audit. |
| U04 | Timing | Trusted `should_end` | TB9/TB10 | Orchestrator accepts simulator `should_end`/`action` as terminal truth. | PARTIAL: simulator quality gate only after run. | Validate terminal action against transcript and expected outcome before ending or scoring. |
| U05 | Injection | User quote echo trap | TB9/TB2/TB11 | User says forbidden/judge-injection text; agent quotes it, causing false penalty or judge influence. | PARTIAL: forbidden checker only scans agent messages but does not distinguish quotation. | Quotation-aware forbidden checks; structured judge transcript. |

## TB10 — Conversation ledger / mutability / event order

| ID | Category | Name | Boundary | How it works | Current defense | Fix needed |
|---|---|---|---|---|---|---|
| L01 | Exploit | Conversation mutation by agent | TB10 | `agent.respond(self.conversation)` receives a mutable conversation; malicious agent can edit prior messages before scoring. | NO. | Pass read-only deep copy to agent; keep append-only internal ledger. |
| L02 | Evasion | Delete prior forbidden output | TB10/TB11 | Agent removes or edits earlier agent messages containing violations. | NO. | Append-only signed event log; scorer ignores mutable agent-side copies. |
| L03 | Exploit | Reorder/fake turn numbers | TB10/TB11 | Agent mutates message order or turn values to fake compliance order. | NO/PARTIAL: orchestrator assigns new messages, but prior object is mutable. | Monotonic sequence numbers assigned by orchestrator; immutable messages. |
| L04 | Exploit | Attach tool calls to old messages | TB10/TB11 | Agent adds successful-looking tool calls to earlier turns to satisfy ordering. | PARTIAL: fabricated-ID check may catch nonexistent IDs, not mutated real IDs/order. | Canonical tool event ledger with message/event linkage. |
| L05 | Timing | Same-turn ambiguity | TB10 | System, agent, user messages can share turn numbers; tool events lack finer ordering. | PARTIAL. | Global event sequence: request → policy_check → execute/block → observe → score. |
| L06 | Exploit | `post_call` ignored inconsistently | TB16/TB11/TB12 | Metadata is set, but many scorers and LLM formatting do not filter it. | NO. | Single scorer transcript builder enforcing all metadata exclusions. |
| L07 | Timing | Missing policy-check event | TB10 | Ledger has `ToolCall`, but no explicit `policy_check` event proving checks happened before execution. | NO. | Add event types and assert event-order contract at scoring time. |
| L08 | Exploit | Raw vs modified transcript collapse | TB8/TB10 | Harness-modified text replaces raw text, so scorer cannot distinguish agent skill from harness assistance. | NO. | Store raw agent output, harness transformations, final delivered text as separate linked events. |
| L09 | Exploit | Source field is data, not provenance | TB10/TB11 | `ToolCall.source` is a mutable string inside the object being scored. | PARTIAL. | Provenance assigned by ledger, not carried from agent-returned object. |
| L10 | Audit | Non-deterministic IDs/state | TB5/TB10 | Random IDs/coupon codes without deterministic seed make audit and replay harder. | PARTIAL. | Seeded RNG per trace or record randomness as events. |

## TB11 — Rule scorer / deterministic scoring

| ID | Category | Name | Boundary | How it works | Current defense | Fix needed |
|---|---|---|---|---|---|---|
| R01 | Exploit | Tool-name-only success | TB11 | Required tool check counts tool name success, not full argument semantics or consent state. | PARTIAL: order ID checked for many tools. | Required-tool predicates include args, order, customer consent, and result schema. |
| R02 | Exploit | Final-state-not-causal | TB5/TB11 | Scorer checks final DB state rather than proving the agent caused the transition. | PARTIAL. | Compare initial/final diffs tied to tool event IDs. |
| R03 | Exploit | Confirmed outcome weak causal check | TB11 | `confirmed` checks order status but does not require successful `update_delivery_status` unless scenario lists it. | PARTIAL. | Every terminal outcome requires a causal successful event or explicit no-tool evidence. |
| R04 | Exploit | Hard-score dilution | TB11 | Many easy rule checks can dilute one serious failure in average `hard_score`. | PARTIAL: outcome cap and severity penalty exist. | Critical failures impose caps/fail-fast, not small average penalties. |
| R05 | Evasion | Severity penalty too small | TB11 | A critical forbidden behavior subtracts only 0.05 from overall. | PARTIAL. | Severity-based score caps, e.g. critical ≤40, major ≤70, depending on task. |
| R06 | Injection | Branch result driven by LLM | TB12/TB11 | Branch accuracy relies on `branch_taken` returned by judge, which can be influenced by transcript text. | PARTIAL: full mode lists valid labels. | Derive branches from user simulator action/state or deterministic event predicates. |
| R07 | Evasion | Optional branch N/A manipulation | TB11 | If branch_taken is missing or wrong, optional target steps may become `not_applicable`. | PARTIAL. | Reachability should come from trusted branch state, not judge output. |
| R08 | Exploit | Rule fallback keyword stuffing | TB11 | If LLM fails, fallback marks steps complete based on keyword overlap. | PARTIAL. | Official score should be invalid/withheld on judge failure or use event-based fallback only. |
| R09 | Exploit | Opening/closing substring thresholds | TB11 | Agent passes protocol checks with partial fragments. | PARTIAL. | Ordered semantic template checks with negative cases. |
| R10 | Evasion | Exact forbidden matching | TB11 | Simple substring matching misses variants. | PARTIAL. | Normalized regex + semantic detectors for critical categories. |
| R11 | Evasion/Exploit | Step forbidden not step-scoped | TB11 | Step-level forbidden words are checked against all agent messages, not only the relevant step context. | PARTIAL. | Scope forbidden checks to active step windows, while keeping global forbidden checks separate. |
| R12 | Timing | No-LLM/fast/full comparability | TB15/TB11 | Score weights change depending on whether LLM checks exist or branch score is `None`. | PARTIAL. | Mark scoring mode; calibrate/normalize or separate leaderboards. |
| R13 | Exploit | `no_answer` / callback weak outcome | TB11 | Outcomes outside refund/reschedule/confirmed/escalated fall through to basic log matching. | PARTIAL. | Outcome-specific validators for every allowed terminal result. |
| R14 | Exploit | Conversation success not canonical ledger | TB10/TB11 | Scorer builds `successful_tools` from conversation tool calls, not directly from immutable simulator ledger. | PARTIAL: fabricated-ID check mutates obvious fakes first. | Scorer reads canonical ledger only. |
| R15 | Audit | Checks lack event-level evidence | TB11/TB14 | Many `CheckResult`s have explanations but no specific event ID/text span. | NO/PARTIAL. | Every check must reference event IDs and raw evidence spans. |

## TB12/TB13 — LLM judge and parser

| ID | Category | Name | Boundary | How it works | Current defense | Fix needed |
|---|---|---|---|---|---|---|
| J01 | Injection | Transcript instruction injection | TB12 | Agent/user/tool text tells judge to ignore rubric or output high scores. | PARTIAL: prompt warning. | Structured escaped transcript; adversarial prompt-injection tests; judge sees events, not free-form logs. |
| J02 | Injection | Role delimiter spoof | TB12 | Transcript labels are plain text, so content can mimic roles/turns/system lines. | NO. | JSON array of `{role, turn, content}` with escaped content. |
| J03 | Injection | Tool result injection | TB6/TB12 | Tool result or mock contains judge instructions or fake evidence. | PARTIAL. | Treat tool outputs as untrusted fields; truncate and quote. |
| J04 | Injection | Scenario field injection | TB1/TB12 | Scenario description/steps contain instructions affecting judge. | PARTIAL. | Separate trusted rubric prompt from untrusted scenario data; validate scenario source. |
| J05 | Injection | JSON extraction poisoning | TB13 | Judge output with extra braces or echoed transcript breaks `raw.find("{")` / `rfind("}")`. | PARTIAL: exception returns `{}`. | Use structured response API or strict JSON parser with no free text. |
| J06 | Exploit | Fast-mode missing binary penalties | TB13 | Fast parser only creates binary items returned by judge; omitted negative items disappear. | NO/PARTIAL. | Fill all binary items with default validated values; missing required fields = judge failure. |
| J07 | Exploit | Fast-mode branch under-validation | TB13 | Fast mode accepts `branch_taken` without validating against legal labels. | NO. | Same branch label validation as full mode. |
| J08 | Exploit | Judge failure fallback | TB13/TB11 | Agent-induced judge failure can trigger keyword fallback that is easier to game, or produce invalid-but-still-visible score. | PARTIAL: failure summary marks judge failure and orchestrator sets `run_validity=invalid`. | Official score withheld on judge failure; no fallback for leaderboard unless event-based. |
| J09 | Audit | Hallucinated evidence | TB13/TB14 | Judge may cite turns/evidence that do not actually support score. | PARTIAL: evidence fields are stored but not verified. | Validate evidence turns/spans exist and support claimed event type where possible. |
| J10 | Injection | Token flooding / context truncation | TB12 | Agent/tool args produce long text that pushes bad evidence out of the LLM context. | NO. | Token budgets, per-field truncation, event summaries with hashes. |
| J11 | Evasion | Boolean parser quirks | TB13 | `_parse_bool` treats unexpected strings as false, which can suppress binary penalties if judge outputs nonstandard text. | PARTIAL. | Strict boolean schema; non-boolean = judge error. |
| J12 | Injection | System messages included in LLM transcript | TB8/TB12 | LLM is told system messages are not agent evidence, but they remain in transcript and can affect judgment. | PARTIAL. | Exclude system reminders from judge input or put them in a separate non-evidence field. |

## TB14/TB15 — Reports, traces, downstream consumers, scoring modes

| ID | Category | Name | Boundary | How it works | Current defense | Fix needed |
|---|---|---|---|---|---|---|
| D01 | Injection | Trace/dashboard XSS or Markdown injection | TB14 | Agent text stored in trace may be rendered unsafely by a dashboard. | NO in shown code. | Escape on render; sanitize trace display fields; content security policy. |
| D02 | Evasion/Privacy | PII leakage in trace | TB14 | `db_state` includes phone/address/order details in saved JSON. | NO. | Redact or encrypt PII; separate audit trace from public leaderboard trace. |
| D03 | Injection | Diagnosis prompt injection | TB14 | `diagnose_failure(... use_llm=...)` receives conversation/report and may be prompt-injected. | PARTIAL/UNKNOWN: diagnosis code not shown. | Apply same structured transcript and untrusted-data isolation to diagnosis. |
| D04 | Exploit | Invalid run still looks scored | TB14/TB15 | Orchestrator sets `run_validity=invalid` for judge failures, but score fields remain present and `official` is not clearly recomputed. | PARTIAL. | If invalid, set `official=False`, withhold `overall_score_100`, and separate diagnostic score. |
| D05 | Audit | Trace tampering | TB14 | Saved JSON trace has no signature or hash chain. | NO. | Hash/sign event ledger and report; include reproducibility metadata. |
| D06 | Exploit | Mode shopping | TB15 | Different modes (`use_harness`, no LLM, fast mode) produce non-comparable scores; a participant may choose easier mode if allowed. | PARTIAL: metadata records mode. | Enforce official mode; separate assisted/unassisted and fast/full leaderboards. |

---

# Part 3: Defense Completeness Matrix

Defense layer legend:

| Layer | Meaning |
|---|---|
| D1 | Schema/model validation and normalization |
| D2 | Harness gates, regeneration, sanitization, reminders |
| D3 | Pre-execution policy and rollback sequencing |
| D4 | Tool simulator business validation |
| D5 | Canonical ledger / provenance / event-order enforcement |
| D6 | Rule scorer, DB cross-validation, caps |
| D7 | LLM prompt hardening and response validation |
| D8 | Audit/trace/downstream controls |

Coverage symbols: **✓** covered; **◐** partial/brittle/config-dependent; **—** gap.

## Matrix A — Scenario and agent-text attacks

| Vector | D1 | D2 | D3 | D4 | D5 | D6 | D7 | D8 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| S01 Pre-seeded success state | ◐ | — | — | — | — | ◐ | — | — |
| S02 Scenario prompt injection | ◐ | — | — | — | — | — | ◐ | — |
| S03 Custom tool collision | — | — | — | — | — | — | — | — |
| S04 Mock result poisoning | — | — | — | — | — | — | ◐ | — |
| S05 Tool fault overwrite | — | — | — | — | — | — | — | — |
| S06 Impossible expected steps | — | — | — | — | — | ◐ | — | — |
| S07 Degenerate templates | ◐ | — | — | — | — | ◐ | — | — |
| S08 Seed column/schema poison | ◐ | — | — | ◐ | — | — | — | — |
| S09 Unknown expected result | — | — | — | — | — | ◐ | — | — |
| A01 Opening stuffing | — | — | — | — | — | ◐ | ◐ | — |
| A02 Closing stuffing | — | ◐ | — | — | — | ◐ | ◐ | — |
| A03 Post-call scoring leak | — | — | — | — | — | — | — | — |
| A04 Forbidden homoglyph bypass | — | ◐ | — | — | — | ◐ | — | — |
| A05 Forbidden paraphrase bypass | — | — | — | — | — | — | ◐ | — |
| A06 End-call synonym bypass | — | ◐ | — | — | — | ◐ | — | — |
| A07 Repetition-threshold gaming | — | — | — | — | — | ◐ | ◐ | — |
| A08 Length splitting | — | — | — | — | — | ◐ | — | — |
| A09 Length denominator/post-call bug | — | — | — | — | — | — | — | — |
| A10 Template leak variant | — | ◐ | — | — | — | ◐ | ◐ | — |
| A11 Final self-certification | — | ◐ | — | — | — | ◐ | ◐ | — |
| A12 Transcript role spoofing | — | — | — | — | — | — | ◐ | — |
| A13 Direct judge injection | — | — | — | — | — | — | ◐ | — |
| A14 JSON/brace poisoning | — | — | — | — | — | ◐ | ◐ | — |
| A15 Tool-claim without tool | — | — | — | — | — | ◐ | ◐ | — |
| A16 User-quote laundering | — | — | — | — | — | — | ◐ | — |
| A17 System reminder echo | — | ◐ | — | — | — | — | ◐ | — |
| A18 Max-turn stalling | — | — | — | — | — | ◐ | ◐ | — |

Major gaps from Matrix A: post-call text handling, transcript serialization, semantic forbidden/ending detection, scenario validation, and harness-vs-agent provenance.

## Matrix B — Tool execution attacks

| Vector | D1 | D2 | D3 | D4 | D5 | D6 | D7 | D8 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| T01 Raw simulator bypass | — | — | — | — | — | — | — | — |
| T02 Fabricated ToolCall | — | — | — | — | ◐ | ◐ | — | — |
| T03 Ledger ID reuse | — | — | — | — | ◐ | ◐ | — | — |
| T04 Mutate real ToolCall | — | — | — | — | — | ◐ | — | — |
| T05 Source spoofing | — | — | — | — | — | ◐ | — | — |
| T06 Unknown/custom tool success | ◐ | — | — | ◐ | — | ◐ | — | — |
| T07 Missing required args | ◐ | — | — | ◐ | — | ◐ | — | — |
| T08 Wrong argument types | ◐ | — | — | ◐ | — | ◐ | — | — |
| T09 Wrong order ID | — | — | — | ✓ | ◐ | ✓ | — | — |
| T10 Missing order ID mock/custom | ◐ | — | — | ◐ | — | ◐ | — | — |
| T11 Query other customer | — | — | — | — | — | — | — | — |
| T12 Negative/zero compensation | — | — | — | ◐ | — | ◐ | — | — |
| T13 Invalid compensation type | — | — | — | — | — | ◐ | — | — |
| T14 Over-budget compensation | — | — | — | ✓ | — | ✓ | — | — |
| T15 Invalid reschedule time | — | — | — | — | — | ◐ | — | — |
| T16 Arbitrary log result | — | — | — | — | — | ◐ | — | — |
| T17 Log before operation | — | ◐ | ◐ | — | — | ◐ | — | — |
| T18 Same-turn batch ambiguity | — | ◐ | ◐ | — | — | ◐ | — | — |
| T19 Post-call side effects before filtering | — | — | — | — | — | ◐ | — | — |
| T20 Fault count gaming | — | — | ◐ | — | — | — | — | — |
| T21 Duplicate log-call manipulation | — | — | — | ✓ | — | ◐ | — | — |
| T22 Tool argument injection | — | — | — | — | — | — | ◐ | — |
| T23 Oversized tool args/results | — | — | — | — | — | — | — | — |
| T24 Mock/state inconsistency | — | — | — | ◐ | — | ◐ | ◐ | — |

Major gaps from Matrix B: raw simulator exposure, immutable/canonical tool records, uniform schema/business validation, post-call pre-execution enforcement, and size/injection controls for tool fields.

## Matrix C — Harness, user simulator, and ledger attacks

| Vector | D1 | D2 | D3 | D4 | D5 | D6 | D7 | D8 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| H01 Harness laundering | — | ◐ | — | — | — | — | — | ◐ |
| H02 Closing injection credit | — | ◐ | — | — | — | — | — | — |
| H03 Sanitization hides forbidden | — | ◐ | — | — | — | — | — | ◐ |
| H04 Harness tool credit confusion | — | ◐ | — | — | ◐ | ◐ | — | ◐ |
| H05 Step progress false completion | — | ◐ | — | — | — | ◐ | ◐ | — |
| H06 Incomplete tool gating | — | ◐ | ◐ | — | — | ◐ | — | — |
| H07 Emotion bypass | — | ◐ | — | — | — | ◐ | ◐ | — |
| H08 Policy after side effect | — | ◐ | ◐ | — | — | — | — | — |
| H09 False regeneration fact | — | — | — | — | — | — | — | — |
| H10 Reminder leakage | — | ◐ | — | — | — | — | ◐ | — |
| H11 Retry turn inflation | — | ✓ | ✓ | — | — | — | — | — |
| U01 User prompt injection | — | — | — | — | — | — | ◐ | — |
| U02 Provoked hang-up | — | — | — | — | — | ◐ | ◐ | — |
| U03 Emotion-label mismatch | — | ◐ | — | — | — | — | ◐ | — |
| U04 Trusted `should_end` | — | — | — | — | — | ◐ | — | — |
| U05 User quote echo trap | — | — | — | — | — | ◐ | ◐ | — |
| L01 Conversation mutation | — | — | — | — | — | — | — | — |
| L02 Delete prior forbidden output | — | — | — | — | — | — | — | — |
| L03 Reorder/fake turns | — | — | — | — | — | — | — | — |
| L04 Attach calls to old messages | — | — | — | — | ◐ | ◐ | — | — |
| L05 Same-turn ambiguity | — | — | ◐ | — | — | ◐ | — | — |
| L06 `post_call` ignored | — | — | — | — | — | — | — | — |
| L07 Missing policy-check event | — | — | — | — | — | — | — | — |
| L08 Raw vs modified collapse | — | ◐ | — | — | — | — | — | — |
| L09 Mutable source provenance | — | — | — | — | — | ◐ | — | — |
| L10 Non-deterministic audit | — | — | — | — | — | — | — | ◐ |

Major gaps from Matrix C: immutable append-only conversation ledger, raw/final/harness provenance, event-level ordering, and consistent metadata filtering.

## Matrix D — Scorer, LLM, and trace attacks

| Vector | D1 | D2 | D3 | D4 | D5 | D6 | D7 | D8 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| R01 Tool-name-only success | — | — | — | ◐ | — | ◐ | — | — |
| R02 Final-state-not-causal | — | — | — | — | — | ◐ | — | — |
| R03 Weak confirmed causal check | — | — | — | — | — | ◐ | — | — |
| R04 Hard-score dilution | — | — | — | — | — | ◐ | — | — |
| R05 Severity penalty too small | — | — | — | — | — | ◐ | — | — |
| R06 LLM-driven branch result | — | — | — | — | — | ◐ | ◐ | — |
| R07 Optional branch N/A manipulation | — | — | — | — | — | ◐ | ◐ | — |
| R08 Keyword fallback stuffing | — | — | — | — | — | ◐ | ◐ | — |
| R09 Opening/closing thresholds | — | — | — | — | — | ◐ | ◐ | — |
| R10 Exact forbidden matching | — | ◐ | — | — | — | ◐ | — | — |
| R11 Step forbidden not step-scoped | — | — | — | — | — | ◐ | — | — |
| R12 Mode comparability | — | — | — | — | — | ◐ | — | ◐ |
| R13 Weak `no_answer`/callback validation | — | — | — | — | — | ◐ | — | — |
| R14 Conversation not canonical ledger | — | — | — | — | ◐ | ◐ | — | — |
| R15 Weak evidence audit | — | — | — | — | — | ◐ | — | ◐ |
| J01 Transcript injection | — | — | — | — | — | — | ◐ | — |
| J02 Role delimiter spoof | — | — | — | — | — | — | — | — |
| J03 Tool result injection | — | — | — | — | — | — | ◐ | — |
| J04 Scenario field injection | ◐ | — | — | — | — | — | ◐ | — |
| J05 JSON extraction poisoning | — | — | — | — | — | — | ◐ | — |
| J06 Fast missing binary penalties | — | — | — | — | — | — | ◐ | — |
| J07 Fast branch under-validation | — | — | — | — | — | — | — | — |
| J08 Judge failure fallback | — | — | — | — | — | ◐ | ◐ | ◐ |
| J09 Hallucinated evidence | — | — | — | — | — | — | ◐ | — |
| J10 Token flooding | — | — | — | — | — | — | — | — |
| J11 Boolean parser quirks | — | — | — | — | — | — | ◐ | — |
| J12 System messages in judge transcript | — | ◐ | — | — | — | — | ◐ | — |
| D01 Trace XSS/Markdown injection | — | — | — | — | — | — | — | — |
| D02 PII leakage in trace | — | — | — | — | — | — | — | — |
| D03 Diagnosis prompt injection | — | — | — | — | — | — | ◐ | — |
| D04 Invalid run still scored | — | — | — | — | — | ◐ | — | ◐ |
| D05 Trace tampering | — | — | — | — | — | — | — | — |
| D06 Mode shopping | — | — | — | — | — | ◐ | — | ◐ |

Major gaps from Matrix D: LLM transcript hardening, strict structured output, causality-based scoring, official invalidation, trace sanitization, and audit evidence.

---

# Part 4: Recommended Defense Architecture

The minimal architecture change is to stop treating the mutable conversation as the source of truth. Make an append-only event ledger the only scoring input, and make all agent/tool/harness activity flow through one gateway.

## P0: Can directly game scores today

### 1. Introduce a single Policy-Enforced Tool Gateway

Close: T01, T03, T04, T05, T17, T18, T19, H08, L04, R14.

Required changes:

- Do not pass raw `OutboundToolSimulator` to the agent.
- Agent receives only a narrow `ToolExecutor` interface.
- Every tool request becomes an event before execution:

```text
agent_tool_request
→ policy_check
→ tool_execute OR tool_block
→ tool_observation
```

- The gateway owns `source`, `id`, `turn`, and sequence number.
- Agent-returned `ToolCall` objects should be treated as requests, not authoritative observations.
- In post-call mode, reject non-`log_call_result` requests before execution, not after `agent.respond`.
- Guarded policy must exist regardless of `use_harness`; harness can add extra policy, but baseline safety cannot depend on harness being enabled.

### 2. Replace mutable conversation scoring with an append-only canonical event ledger

Close: L01-L09, T02-T05, R14, R15, Contract 1/4/5 gaps.

Required ledger event types:

```text
run_started
scenario_loaded
user_message_observed
agent_output_raw
harness_policy_check
harness_block
harness_transform
agent_output_delivered
tool_request
tool_policy_check
tool_executed
tool_blocked
tool_observed
db_state_diff
termination_decision
score_check
run_finished
```

Each event should have:

```text
event_id
seq
turn
actor
event_type
raw_payload_hash
normalized_payload
parent_event_ids
timestamp
```

Scorer should read only canonical ledger events and DB diffs, never mutable `conversation.messages` as truth.

### 3. Preserve raw vs harness-modified text and score them separately

Close: H01, H02, H03, H10, A02, A03, A17, L08.

Required changes:

- Store `raw_agent_text`, `harness_modified_text`, and `delivered_text` as linked events.
- Forbidden behavior checks run on raw text and delivered text.
- Opening/closing credit should be based on raw agent text unless the report is explicitly marked “harness-assisted.”
- Harness interventions should become score-affecting metadata:
  - repeated forbidden blocks,
  - premature tool blocks,
  - forced sanitization,
  - injected closing,
  - forced step reminders.

Minimal policy: an official unassisted score cannot receive credit for harness-injected content.

### 4. Add strict tool schema and business validation before every handler

Close: T07-T16, T22-T24, R01, R13.

Required validation:

- Enforce each tool’s JSON schema at runtime, not only in tool definitions.
- Reject missing `order_id`, wrong `order_id`, wrong types, unknown fields where not allowed.
- Validate:
  - `create_compensation.type ∈ {refund, coupon, redelivery}`,
  - monetary amount is numeric and positive where applicable,
  - amount does not exceed remaining budget,
  - `reschedule_delivery.new_time` has valid format/range,
  - `log_call_result.result` is an enum,
  - `query_customer.customer_phone` matches scenario unless cross-customer lookup is explicitly allowed.
- For custom tools, require a namespace and a validator; custom mock success cannot count for official scoring without a scoring predicate.

### 5. Make outcome scoring causal, not final-state-only

Close: S01, R02, R03, R13, T17, T19.

For each expected outcome, require a causal ledger path.

Examples:

```text
refunded:
  successful create_compensation
  same scenario order_id
  type=refund
  amount>0
  approved DB diff created by that tool event
  later successful log_call_result(result=refunded)

rescheduled:
  successful reschedule_delivery
  valid new_time
  customer-confirmation event before tool call
  schedule DB diff caused by that tool event
  later log_call_result(result=rescheduled)

confirmed:
  successful update_delivery_status(new_status=confirmed)
  customer confirmation event before update
  order status DB diff caused by that event
  later log_call_result(result=confirmed)

escalated:
  successful transfer_to_human
  escalation precondition satisfied
  later log_call_result(result=escalated)

no_answer:
  trusted user-sim initial action=no_answer
  no normal conversation occurred
  log_call_result(result=no_answer) if required
```

### 6. Harden LLM judge input and output

Close: A11-A17, J01-J12, U01, D03.

Required changes:

- Feed the judge a structured JSON transcript, not raw labeled text.
- Escape all user/agent/tool content.
- Exclude system reminders and post-call bookkeeping from evidence unless judging harness behavior.
- Truncate large fields with hashes.
- Use strict schema output:
  - all dimensions required,
  - all binary items required,
  - branch labels must be from legal labels,
  - booleans must be actual booleans,
  - missing/invalid fields invalidate the judge result.
- Do not use keyword fallback for official scores after judge failure. Either withhold official score or use an event-based deterministic fallback.

### 7. Fix post-call exclusion globally

Close: A03, A09, L06.

Implement a single scorer helper:

```python
def scored_agent_messages(conversation):
    return [
        m for m in conversation.messages
        if m.role == Role.AGENT and not m.metadata.get("post_call")
    ]
```

Then use it everywhere:

- opening/closing,
- forbidden checks,
- step-level forbidden checks,
- repetition,
- length,
- LLM transcript,
- step compliance.

Better: replace this helper with ledger event filtering.

### 8. Add score caps for critical violations

Close: R04, R05, H01-H03.

Suggested caps:

| Condition | Max score |
|---|---:|
| Fabricated or mutated tool event | 0 |
| Raw simulator bypass | 0 |
| Critical forbidden behavior in raw delivered output | 40 |
| Critical forbidden behavior blocked only by harness | 70 or assisted-only |
| Failed task outcome | 60, already present |
| Missing required causal outcome event | 60 |
| Judge failure in official LLM mode | withhold official score |
| Trace/event-order contract violation | invalid run |

## P1: Can game scores with moderate effort

### 9. Add full scenario validation

Close: S02-S09, D06.

Validate at scenario load:

- expected steps exist,
- branch targets exist,
- optional branch reachability is coherent,
- expected branch labels are legal,
- expected call result is enum,
- must-call tools are known and supported,
- custom tool names do not collide with built-ins,
- `world_seed` tables and columns are allowlisted,
- forbidden keywords are non-empty after normalization,
- mandatory opening/closing have meaningful extractable elements,
- pre-seeded final outcome states are either forbidden or explicitly marked as initial conditions.

### 10. Normalize and strengthen text detectors

Close: A04-A10, R09-R11, H07.

Create a single normalization pipeline:

```text
NFKC
casefold
remove zero-width chars
collapse whitespace/punctuation
map common confusables
optional simplified/traditional Chinese normalization
```

Then apply it to:

- forbidden behavior detection,
- end-call detection,
- opening/closing template checks,
- repetition checks,
- response-length checks if using normalized characters.

Add semantic detectors for high-risk forbidden behaviors and premature ending.

### 11. Replace branch scoring with trusted branch events

Close: R06, R07, J07.

Branch state should come from:

- user simulator structured action,
- explicit user utterance classifier,
- tool result state,
- scenario branch predicate,

not from free-form LLM `branch_taken`.

LLM can explain branch quality, but deterministic code should decide which branch was actually reachable/taken.

### 12. Add size limits and transcript budgets

Close: T23, J10.

Per-field limits:

- agent message max chars,
- tool arg string max chars,
- tool result max serialized size,
- total judge transcript token budget,
- max events included per run.

Long content should be summarized with:

```text
truncated=true
original_char_count
sha256
first_n_chars
last_n_chars
```

### 13. Make scoring modes explicit and non-comparable by default

Close: R12, D04, D06.

Report fields:

```text
official: bool
score_mode: "full_llm" | "fast_llm" | "rule_only"
assist_mode: "unassisted" | "harness_assisted"
invalid_reason: ...
```

Policy:

- official leaderboard uses one fixed mode,
- invalid runs have no official numeric score,
- harness-assisted and unassisted runs are separate.

## P2: Theoretical, lower-probability, or downstream-only

### 14. Secure trace output and dashboard rendering

Close: D01, D02, D05.

- Escape HTML/Markdown in dashboard.
- Redact phone/address/customer names in public traces.
- Store full PII trace separately with access control.
- Add hash/signature for trace and event ledger.
- Include code version, scenario hash, model config, RNG seed.

### 15. Deterministic replay and fuzzing

Close: L10, S05, hidden timing gaps.

Add property tests asserting the five contracts:

1. No score credit from a tool call unless a matching canonical `tool_observed(success)` event exists.
2. Every state-changing DB diff has a parent successful tool event.
3. Every blocked tool request has no DB diff.
4. Every score check references at least one event ID or explicitly says why no event is needed.
5. Event sequence always satisfies request → policy_check → execute_or_block → observe → score.

Fuzz dimensions:

- malformed tool args,
- reordered tool calls,
- same-turn tool batches,
- Unicode forbidden-word variants,
- prompt injection payloads,
- huge arguments/results,
- post-call side effects,
- scenario DSL inconsistencies.

### 16. Isolate diagnosis and reports from scoring

Close: D03.

`diagnose_failure` should never affect score. If it uses an LLM, give it the same structured, escaped event ledger used by the judge, and mark its output diagnostic-only.

---

## Minimal closure set

The smallest set of changes that closes all high-impact gaps is:

1. **Single tool gateway; no raw simulator access.**
2. **Immutable append-only event ledger as the only scoring source.**
3. **Raw/final/harness provenance separation.**
4. **Strict pre-execution tool schema and business validation.**
5. **Causal outcome scoring from ledger-linked DB diffs.**
6. **Structured LLM judge input/output with strict schema.**
7. **Global post-call/system-message exclusion for agent scoring.**
8. **Critical-failure score caps and invalid-run score withholding.**
9. **Scenario validation and trace sanitization.**

That architecture directly enforces all five contracts:

| Contract | Architectural enforcement |
|---|---|
| Source-of-Truth | Scorer reads canonical event ledger and DB diffs, not agent-supplied conversation objects |
| Execution-Order | Gateway emits policy-check events before execution and blocks side effects pre-execution |
| Outcome-Strictness | Outcome requires explicit successful causal event chain |
| Auditability | Every check references event IDs and raw evidence spans |
| Event-Order | Ledger sequence asserts request → policy_check → execute_or_block → observe → score |
