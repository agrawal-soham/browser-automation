// Structured correlated tracing. One TraceSession per agent run; every significant step is recorded
// as a typed TraceEvent under the same trace_id so the full pipeline is auditable after the fact.
//
// Storage: chrome.storage.local key `nb.trace.<runId>` holds { traceId, sessionId, runId, events }.
// A ring buffer of MAX_RUNS runs is kept; the oldest is evicted when the cap is hit.
// All writes are fire-and-forget: a storage failure never throws in the hot path.
import type { Usage } from '@src/store/types';
import type { TokenUsage } from '@src/llm/stream';

// --------------------------------------------------------------------------- //
// Event types
// --------------------------------------------------------------------------- //

export type ErrorSource =
  | 'LLM_STREAM' // streamTurn() returned reply.error (after all retries)
  | 'LLM_RETRY' // a retry attempt inside streamTurnNow(), before the final failure
  | 'TOOL_REFUSED' // beforeToolCall() blocked the call before dispatch
  | 'TOOL_EXEC' // layer.call() / agentTool() returned isError or threw
  | 'TOOL_INVALID' // the SDK could not parse the model's tool-call JSON
  | 'GUARD_STOP' // Guards fired ERROR_STOP or NO_EFFECT_STOP
  | 'CONTEXT_LIMIT' // run stopped: context too large even after all recovery steps
  | 'OVERFLOW_FATAL' // recoverFromOverflow() exhausted all strategies
  | 'LOOP_ABORTED' // AbortSignal fired (user pressed Stop)
  | 'IMAGES_DROPPED' // model rejected images; request was retried without them
  | 'REASONING_DROPPED' // server refused the reasoning option; run continues without it
  | 'CUT_BEFORE_ANYTHING'; // reply spent its entire allowance on reasoning; nothing reached tool/text

export interface ErrorDetail {
  /** HTTP status code from the provider, when available. */
  status?: number;
  /** Whether the error was a context-window overflow. */
  overflow?: boolean;
  /** Whether the error was a network-level failure (fetch TypeError). */
  network?: boolean;
  /** The failing tool call's ID, for TOOL_* sources. */
  toolCallId?: string;
  /** The tool name, for TOOL_* sources. */
  tool?: string;
  /** Which retry attempt this was, for LLM_RETRY (1-indexed). */
  attempt?: number;
  /** Which guard fired, for GUARD_STOP. */
  guard?: 'errors' | 'no_effect';
}

export type TraceEvent =
  // ── Happy path ──────────────────────────────────────────────────────────────
  | {
      type: 'TASK_START';
      /** The user's first message text (truncated at 500 chars). */
      task: string;
      turnCap: number;
      model: string;
    }
  | {
      type: 'CONTEXT_BUILT';
      /** Total characters of the request (system + messages + tool definitions). */
      chars: number;
      /** Estimated tokens (chars / 4 × countFactor). */
      tokens_est: number;
      /** Number of ModelMessages in the request. */
      messages: number;
      /** Number of image parts included. */
      images: number;
    }
  | {
      type: 'LLM_REQUEST';
      model: string;
      /** Length of the system prompt in characters. */
      system_len: number;
      messages: number;
      /** Names of every tool offered to the model. */
      tools: string[];
    }
  | {
      type: 'LLM_RESPONSE';
      finish: string;
      /** Names of tools the model called, in order. */
      tool_calls: string[];
      usage: TokenUsage | null;
      durationMs: number;
      /** How many wire attempts the SDK made (including retries). */
      attempts: number;
    }
  | {
      type: 'TOOL_DISPATCH';
      toolCallId: string;
      tool: string;
      /** 'read' | 'mutate' | 'wait' | 'state' | 'control' */
      kind: string;
      /** JSON.stringify(args) truncated at 300 chars. */
      args_preview: string;
    }
  | {
      type: 'TOOL_RESULT';
      toolCallId: string;
      tool: string;
      isError: boolean;
      /** Length of the result text, for sizing purposes. */
      text_len: number;
    }
  | {
      type: 'TURN_SUMMARY';
      turn: number;
      /** How many tool calls ran in this turn. */
      outcomes: number;
      /** Whether done was called and the run is ending. */
      done: boolean;
    }
  | {
      type: 'TASK_END';
      status: string;
      outcome?: string;
      turns: number;
      usage: Usage;
    }
  | {
      type: 'CONTEXT_TRIM';
      /** 'elision' | 'compact' | 'shorten' */
      stage: string;
      tokens_before: number;
      tokens_after: number;
    }
  | {
      type: 'OVERFLOW_RECOVER';
      /** Which recovery stage ran: 1 = compact, 2 = shorten. */
      stage: number;
      new_window: number;
    }
  // ── Errors ──────────────────────────────────────────────────────────────────
  | {
      type: 'ERROR';
      source: ErrorSource;
      message: string;
      detail?: ErrorDetail;
    };

// --------------------------------------------------------------------------- //
// Stored record
// --------------------------------------------------------------------------- //

export interface TraceRecord {
  trace_id: string;
  session_id: string;
  run_id: string;
  timestamp: string;
  /** Current turn number at the time of this event (0 before the first turn starts). */
  turn: number;
  event: TraceEvent;
}

// --------------------------------------------------------------------------- //
// Session
// --------------------------------------------------------------------------- //

export interface TraceSession {
  traceId: string;
  sessionId: string;
  runId: string;
  /** Current turn counter; updated by the loop before each stream call. */
  turn: number;
}

const MAX_RUNS = 50;
const RUNS_INDEX_KEY = 'nb.trace._index';

/** Generates a random UUID, falling back to a timestamp-based ID in environments without crypto. */
function generateId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

/** Creates a new TraceSession for one agent run. Call once at the start of AgentLoop.run(). */
export function createTraceSession(conversationId: string, runId: string): TraceSession {
  return { traceId: generateId(), sessionId: conversationId, runId, turn: 0 };
}

/** Returns the HTTP header object to inject into upstream LLM calls for context propagation. */
export function getTraceHeader(session: TraceSession): Record<string, string> {
  return { 'X-Trace-Id': session.traceId };
}

// --------------------------------------------------------------------------- //
// Event recording
// --------------------------------------------------------------------------- //

/**
 * Records one event. Fire-and-forget: storage errors are silently swallowed so
 * a tracing failure never interrupts the agent.
 */
export function addEvent(session: TraceSession, event: TraceEvent): void {
  const record: TraceRecord = {
    trace_id: session.traceId,
    session_id: session.sessionId,
    run_id: session.runId,
    timestamp: new Date().toISOString(),
    turn: session.turn,
    event,
  };
  void persistRecord(session.runId, record);
}

// --------------------------------------------------------------------------- //
// Persistence
// --------------------------------------------------------------------------- //

const storage = () => globalThis.chrome?.storage?.local;

async function persistRecord(runId: string, record: TraceRecord): Promise<void> {
  const store = storage();
  if (!store) return;
  const key = `nb.trace.${runId}`;
  try {
    const existing = (await store.get([key, RUNS_INDEX_KEY])) as Record<string, unknown>;
    const stored = existing[key] as { traceId: string; sessionId: string; runId: string; events: TraceRecord[] } | undefined;
    const index = (existing[RUNS_INDEX_KEY] as string[] | undefined) ?? [];

    const events = stored?.events ?? [];
    events.push(record);

    // Register this run in the index if new.
    if (!index.includes(runId)) {
      index.push(runId);
      // Evict oldest runs beyond the cap.
      const toEvict = index.splice(0, Math.max(0, index.length - MAX_RUNS));
      if (toEvict.length) {
        await store.remove(toEvict.map(id => `nb.trace.${id}`)).catch(() => undefined);
      }
    }

    await store.set({
      [key]: {
        traceId: record.trace_id,
        sessionId: record.session_id,
        runId: record.run_id,
        events,
      },
      [RUNS_INDEX_KEY]: index,
    });
  } catch {
    // Tracing must never break the agent.
  }
}

// --------------------------------------------------------------------------- //
// Export / retrieval
// --------------------------------------------------------------------------- //

/** Returns all stored sessions as a map of runId → { traceId, events[] }. */
export async function listTraceSessions(): Promise<
  Map<string, { traceId: string; sessionId: string; events: TraceRecord[] }>
> {
  const store = storage();
  const result = new Map<string, { traceId: string; sessionId: string; events: TraceRecord[] }>();
  if (!store) return result;
  try {
    const index = ((await store.get(RUNS_INDEX_KEY)) as Record<string, unknown>)[RUNS_INDEX_KEY] as string[] | undefined;
    if (!index?.length) return result;
    const keys = index.map(id => `nb.trace.${id}`);
    const data = (await store.get(keys)) as Record<string, unknown>;
    for (const id of index) {
      const val = data[`nb.trace.${id}`] as { traceId: string; sessionId: string; events: TraceRecord[] } | undefined;
      if (val) result.set(id, val);
    }
  } catch {
    // Best-effort.
  }
  return result;
}

/**
 * Exports all events for a single run as JSONL (one JSON object per line).
 * Returns an empty string when the run is not found.
 */
export async function exportTraceAsJsonl(runId: string): Promise<string> {
  const store = storage();
  if (!store) return '';
  try {
    const key = `nb.trace.${runId}`;
    const data = (await store.get(key)) as Record<string, unknown>;
    const val = data[key] as { events: TraceRecord[] } | undefined;
    if (!val) return '';
    return val.events.map(e => JSON.stringify(e)).join('\n');
  } catch {
    return '';
  }
}

/** Clears all trace data from storage. */
export async function clearAllTraces(): Promise<void> {
  const store = storage();
  if (!store) return;
  try {
    const index = ((await store.get(RUNS_INDEX_KEY)) as Record<string, unknown>)[RUNS_INDEX_KEY] as string[] | undefined;
    const keys = (index ?? []).map(id => `nb.trace.${id}`);
    await store.remove([...keys, RUNS_INDEX_KEY]);
  } catch {
    // Best-effort.
  }
}
