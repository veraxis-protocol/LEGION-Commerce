// AG Studio AgLlmAdapter -> LEGION backend (/api/llm/turn) -> NVIDIA NIM. No credential is held in the browser.
export function legionAdapter() {
  return {
    executeTurn(request, options = {}) {
      const complete = fetch('/api/llm/turn', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
        signal: options.signal,
      }).then((r) => {
        if (!r.ok) throw new Error(`LLM backend HTTP ${r.status}`);
        return r.json();
      });
      async function* events() {
        const res = await complete;
        for (const item of res.output || []) {
          if (item.type === 'message') {
            yield { type: 'TEXT_MESSAGE_START', messageId: item.id, role: 'assistant' };
            for (const c of item.content || []) if (c.type === 'text') yield { type: 'TEXT_MESSAGE_CONTENT', messageId: item.id, delta: c.text };
            yield { type: 'TEXT_MESSAGE_END', messageId: item.id };
          } else if (item.type === 'function_call') {
            yield { type: 'TOOL_CALL_START', toolCallId: item.callId, toolCallName: item.name };
            yield { type: 'TOOL_CALL_ARGS', toolCallId: item.callId, delta: item.arguments };
            yield { type: 'TOOL_CALL_END', toolCallId: item.callId };
          }
        }
      }
      return { stream: { [Symbol.asyncIterator]: () => events() }, complete };
    },
  };
}
