import { useCallback, useEffect, useRef, useState } from "react";
import { postJson } from "./api";
import { AdvisorModelOutputDisclaimer } from "./advisorDisclaimer.jsx";

const SUGGESTED_QUESTIONS = [
  "How did my portfolio change in the last 7 days?",
  "How did my portfolio change in the last day?",
  "Is my portfolio balanced on sectors, ETFs, and single-stock weights?",
  "How should I rebalance my portfolio?",
  "Which tickers moved my weights the most?",
  "What is 5-25 rebalancing rule?",
];

const SUGGESTED_QUESTIONS_LIFE_PLAN = [
  ...SUGGESTED_QUESTIONS,
  "Compare drift between my growth and retirement portfolios here.",
];

/**
 * Mr Brown — floating chat for portfolio / net worth / life planner pages.
 */
export function MrBrownChat({
  userId,
  page,
  portfolioId = null,
  linkedPortfolioIds = null,
  growthPortfolioId = null,
  retirementPortfolioId = null,
  title = "Hi I am Mr. Brown, your AI assistant",
}) {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const endRef = useRef(null);

  const suggestions = page === "life_plan" ? SUGGESTED_QUESTIONS_LIFE_PLAN : SUGGESTED_QUESTIONS;

  const scrollToEnd = () => {
    try {
      endRef.current?.scrollIntoView({ behavior: "smooth" });
    } catch {
      /* ignore */
    }
  };

  useEffect(() => {
    if (open) scrollToEnd();
  }, [messages, open]);

  const sendWithText = useCallback(
    async (rawText) => {
      const text = String(rawText || "").trim();
      if (!text || !userId) return;
      setError("");
      let nextMsgs = [];
      setMessages((prev) => {
        nextMsgs = [...prev, { role: "user", content: text }];
        return nextMsgs;
      });
      setLoading(true);
      try {
        const history = nextMsgs.slice(-12).map((m) => ({ role: m.role, content: m.content }));
        const payload = {
          user_id: userId,
          page,
          message: text,
          history,
        };
        if (portfolioId) payload.portfolio_id = portfolioId;
        if (Array.isArray(linkedPortfolioIds) && linkedPortfolioIds.length) payload.portfolio_ids = linkedPortfolioIds;
        if (growthPortfolioId) payload.growth_portfolio_id = growthPortfolioId;
        if (retirementPortfolioId) payload.retirement_portfolio_id = retirementPortfolioId;
        const res = await postJson("/api/mr-brown/chat", payload);
        const reply = typeof res.reply === "string" ? res.reply : "";
        setMessages((prev) => [...prev, { role: "assistant", content: reply || "(No reply)" }]);
      } catch (e) {
        setError(e?.message || "Request failed");
        setMessages((prev) => [...prev, { role: "assistant", content: "Something went wrong. Try again." }]);
      } finally {
        setLoading(false);
      }
    },
    [userId, page, portfolioId, linkedPortfolioIds, growthPortfolioId, retirementPortfolioId],
  );

  const send = async () => {
    const text = String(input || "").trim();
    if (!text) return;
    setInput("");
    await sendWithText(text);
  };

  if (!userId) return null;

  return (
    <div
      className="mr-brown-chat-root"
      style={{
        position: "fixed",
        right: 16,
        bottom: 16,
        zIndex: 50,
        width: open ? "min(460px, calc(100vw - 32px))" : "auto",
        display: "flex",
        justifyContent: "flex-end",
      }}
    >
      {!open ? (
        <button type="button" className="form-primary-btn mr-brown-chat-open" onClick={() => setOpen(true)}>
          Ask Mr Brown
        </button>
      ) : (
        <div
          className="mr-brown-chat-panel"
          style={{
            display: "flex",
            flexDirection: "column",
            height: "calc(100vh - 32px)",
            maxHeight: "calc(100vh - 32px)",
            background: "var(--surface-elevated)",
            border: "1px solid var(--border-soft)",
            borderRadius: 10,
            boxShadow: "0 8px 32px rgba(0,0,0,0.4)",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "10px 12px",
              borderBottom: "1px solid var(--border-soft)",
              background: "var(--surface)",
            }}
          >
            <span
              style={{
                fontWeight: 600,
                fontSize: 13,
                lineHeight: 1.35,
                color: "var(--text)",
                flex: 1,
                minWidth: 0,
                paddingRight: 8,
              }}
            >
              {title}
            </span>
            <button type="button" className="choice-btn" style={{ padding: "2px 8px", fontSize: 12 }} onClick={() => setOpen(false)}>
              Close
            </button>
          </div>
          <div style={{ flex: 1, overflowY: "auto", padding: 10, fontSize: 13, color: "var(--text)" }}>
            {messages.length === 0 ? (
              <div>
                <p style={{ margin: "0 0 8px", color: "var(--text-muted)", lineHeight: 1.45, fontSize: 12 }}>
                  Tap a suggested question below or type your own. Replies use your saved valuations and target weights.
                </p>
                <AdvisorModelOutputDisclaimer className="mr-brown-advisor-disclaimer" />
              </div>
            ) : (
              messages.map((m, i) => (
                <div
                  key={i}
                  style={{
                    marginBottom: 10,
                    padding: "8px 10px",
                    borderRadius: 8,
                    background: m.role === "user" ? "var(--surface)" : "transparent",
                    border: m.role === "user" ? "1px solid var(--border-soft)" : "none",
                    whiteSpace: "pre-wrap",
                    lineHeight: 1.45,
                  }}
                >
                  <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>
                    {m.role === "user" ? "You" : "Mr Brown"}
                  </div>
                  {m.content}
                  {m.role === "assistant" ? (
                    <AdvisorModelOutputDisclaimer variant="mrBrown" className="mr-brown-advisor-disclaimer" />
                  ) : null}
                </div>
              ))
            )}
            {loading ? <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Thinking…</div> : null}
            {error ? <div style={{ fontSize: 12, color: "#ef4444", marginTop: 6 }}>{error}</div> : null}
            <div ref={endRef} />
          </div>
          <div style={{ padding: "8px 10px", borderTop: "1px solid var(--border-soft)", background: "var(--surface)" }}>
            <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 6 }}>Suggested</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 8 }}>
              {suggestions.map((q) => (
                <button
                  key={q}
                  type="button"
                  className="choice-btn"
                  style={{ fontSize: 11, padding: "4px 8px" }}
                  disabled={loading}
                  onClick={() => sendWithText(q)}
                >
                  {q}
                </button>
              ))}
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send();
                  }
                }}
                placeholder="Ask about drift, drivers, or rebalancing…"
                rows={2}
                style={{
                  flex: 1,
                  resize: "vertical",
                  minHeight: 44,
                  fontSize: 13,
                  padding: 8,
                  borderRadius: 6,
                  border: "1px solid var(--border-soft)",
                  background: "var(--surface-elevated)",
                  color: "var(--text)",
                }}
              />
              <button type="button" className="form-primary-btn mr-brown-chat-send" disabled={loading || !input.trim()} onClick={send} title="Send">
                Send
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
