import { useState, useRef, useEffect } from "react";

const PORTFOLIOS = [
  { id: 1, name: "Growth Portfolio", value: "$124,830", change: "+3.2%", positive: true, assets: ["NVDA", "MSFT", "AMZN"] },
  { id: 2, name: "Dividend Shield", value: "$87,440", change: "+0.8%", positive: true, assets: ["JNJ", "KO", "VZ"] },
  { id: 3, name: "Crypto Exposure", value: "$31,200", change: "-5.4%", positive: false, assets: ["BTC", "ETH", "SOL"] },
  { id: 4, name: "Bonds & Safety", value: "$200,000", change: "+0.2%", positive: true, assets: ["TLT", "BND", "TIPS"] },
];

const SUGGESTED = [
  "Analyze my Growth Portfolio risk",
  "Best dividend stocks for 2025?",
  "Should I rebalance now?",
  "Explain dollar-cost averaging",
];

const INITIAL_MESSAGES = [
  {
    id: 1,
    role: "assistant",
    text: "Good morning, Alex. Markets opened mixed today — your Growth Portfolio is up **+3.2%** while Crypto Exposure faces headwinds.\n\nWhat would you like to explore today?",
    time: "9:31 AM",
  },
];

function TypingDots() {
  return (
    <div style={{ display: "flex", gap: "5px", padding: "4px 0", alignItems: "center" }}>
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          style={{
            width: 7,
            height: 7,
            borderRadius: "50%",
            background: "#c8a96e",
            display: "inline-block",
            animation: `typingBounce 1.2s ease-in-out ${i * 0.2}s infinite`,
          }}
        />
      ))}
    </div>
  );
}

function renderText(text) {
  return text.split(/\*\*(.*?)\*\*/g).map((part, i) =>
    i % 2 === 1 ? <strong key={i} style={{ color: "#c8a96e" }}>{part}</strong> : part
  );
}

export default function InvestmentApp() {
  const [messages, setMessages] = useState(INITIAL_MESSAGES);
  const [input, setInput] = useState("");
  const [isTyping, setIsTyping] = useState(false);
  const [activePortfolio, setActivePortfolio] = useState(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const bottomRef = useRef(null);
  const textareaRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isTyping]);

  const simulateResponse = (userMsg) => {
    setIsTyping(true);
    const responses = [
      "Based on current market conditions, your portfolio allocation looks **well-diversified**. The 60/40 split between equities and fixed income provides a solid risk-adjusted return profile.\n\nKey considerations:\n• Inflation-adjusted returns remain your primary challenge\n• Consider trimming **crypto exposure** below 5% of total holdings\n• Tech sector concentration may warrant rebalancing",
      "The **S&P 500** is trading at a forward P/E of ~21x — slightly elevated versus the 10-year average of 18x. That said, earnings growth forecasts of **+8.4% YoY** provide fundamental support.\n\nFor your risk tolerance, I'd recommend:\n• Maintain current equity allocation\n• Add defensive positions in **healthcare and utilities**\n• Keep 6 months of expenses in liquid assets",
      "Dollar-cost averaging (DCA) is one of the most **psychologically sound** investment strategies. By investing fixed amounts on a regular schedule, you remove timing risk entirely.\n\n**Example:** Investing $500/month in MSFT over 3 years would have yielded a **+127% return** versus trying to time the market optimally.",
    ];
    const delay = 1200 + Math.random() * 800;
    setTimeout(() => {
      setIsTyping(false);
      setMessages((prev) => [
        ...prev,
        {
          id: Date.now(),
          role: "assistant",
          text: responses[Math.floor(Math.random() * responses.length)],
          time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        },
      ]);
    }, delay);
  };

  const handleSend = (msg) => {
    const text = (msg || input).trim();
    if (!text) return;
    const newMsg = { id: Date.now(), role: "user", text, time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) };
    setMessages((prev) => [...prev, newMsg]);
    setInput("");
    simulateResponse(text);
  };

  const handleKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@300;400;500;600&family=DM+Mono:wght@300;400;500&display=swap');

        * { box-sizing: border-box; margin: 0; padding: 0; }

        body {
          font-family: 'DM Mono', monospace;
          background: #0a0a0a;
          color: #e8e0d0;
          height: 100vh;
          overflow: hidden;
        }

        @keyframes typingBounce {
          0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
          30% { transform: translateY(-6px); opacity: 1; }
        }

        @keyframes fadeSlideIn {
          from { opacity: 0; transform: translateY(10px); }
          to { opacity: 1; transform: translateY(0); }
        }

        @keyframes pulse {
          0%, 100% { opacity: 0.6; }
          50% { opacity: 1; }
        }

        .app-shell {
          display: flex;
          height: 100vh;
          background: #0a0a0a;
        }

        /* SIDEBAR */
        .sidebar {
          width: 260px;
          min-width: 260px;
          background: #0e0e0e;
          border-right: 1px solid #1e1e1e;
          display: flex;
          flex-direction: column;
          padding: 28px 20px;
          gap: 28px;
          transition: width 0.3s ease, min-width 0.3s ease, padding 0.3s ease;
          overflow: hidden;
          position: relative;
        }

        .sidebar.collapsed {
          width: 0;
          min-width: 0;
          padding: 0;
        }

        .user-block {
          display: flex;
          flex-direction: column;
          gap: 10px;
          padding-bottom: 22px;
          border-bottom: 1px solid #1a1a1a;
        }

        .user-greeting {
          font-family: 'Cormorant Garamond', serif;
          font-size: 11px;
          font-weight: 400;
          letter-spacing: 0.18em;
          text-transform: uppercase;
          color: #555;
        }

        .user-name {
          font-family: 'Cormorant Garamond', serif;
          font-size: 22px;
          font-weight: 500;
          color: #e8e0d0;
          line-height: 1;
        }

        .user-badge {
          display: inline-flex;
          align-items: center;
          gap: 5px;
          font-size: 9px;
          letter-spacing: 0.1em;
          text-transform: uppercase;
          color: #c8a96e;
          background: #c8a96e18;
          border: 1px solid #c8a96e30;
          padding: 3px 8px;
          border-radius: 2px;
          width: fit-content;
        }

        .user-badge::before {
          content: '';
          width: 5px;
          height: 5px;
          background: #c8a96e;
          border-radius: 50%;
          animation: pulse 2s ease infinite;
        }

        .section-label {
          font-size: 9px;
          letter-spacing: 0.2em;
          text-transform: uppercase;
          color: #3a3a3a;
          margin-bottom: 10px;
        }

        .portfolio-list {
          display: flex;
          flex-direction: column;
          gap: 8px;
          overflow-y: auto;
          flex: 1;
        }

        .portfolio-list::-webkit-scrollbar { width: 2px; }
        .portfolio-list::-webkit-scrollbar-track { background: transparent; }
        .portfolio-list::-webkit-scrollbar-thumb { background: #2a2a2a; }

        .portfolio-card {
          padding: 12px 14px;
          border: 1px solid #1e1e1e;
          border-radius: 4px;
          cursor: pointer;
          transition: all 0.2s ease;
          background: #111;
          position: relative;
          overflow: hidden;
        }

        .portfolio-card::before {
          content: '';
          position: absolute;
          left: 0; top: 0; bottom: 0;
          width: 2px;
          background: transparent;
          transition: background 0.2s ease;
        }

        .portfolio-card:hover, .portfolio-card.active {
          border-color: #2a2a2a;
          background: #141414;
        }

        .portfolio-card.active::before {
          background: #c8a96e;
        }

        .portfolio-card-top {
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
          margin-bottom: 8px;
        }

        .portfolio-name {
          font-size: 11px;
          color: #ccc;
          line-height: 1.3;
          max-width: 120px;
        }

        .portfolio-change {
          font-size: 10px;
          font-weight: 500;
          padding: 2px 5px;
          border-radius: 2px;
        }

        .portfolio-change.pos { color: #6fcf97; background: #6fcf9715; }
        .portfolio-change.neg { color: #eb5757; background: #eb575715; }

        .portfolio-value {
          font-family: 'Cormorant Garamond', serif;
          font-size: 18px;
          font-weight: 500;
          color: #e8e0d0;
          line-height: 1;
          margin-bottom: 8px;
        }

        .portfolio-assets {
          display: flex;
          gap: 4px;
          flex-wrap: wrap;
        }

        .asset-chip {
          font-size: 8px;
          letter-spacing: 0.08em;
          color: #555;
          background: #1a1a1a;
          border: 1px solid #222;
          padding: 2px 5px;
          border-radius: 2px;
        }

        .sidebar-total {
          padding: 14px;
          background: #111;
          border: 1px solid #c8a96e20;
          border-radius: 4px;
        }

        .sidebar-total-label {
          font-size: 9px;
          letter-spacing: 0.15em;
          text-transform: uppercase;
          color: #555;
          margin-bottom: 5px;
        }

        .sidebar-total-value {
          font-family: 'Cormorant Garamond', serif;
          font-size: 24px;
          font-weight: 500;
          color: #c8a96e;
        }

        /* MAIN CHAT AREA */
        .main {
          flex: 1;
          display: flex;
          flex-direction: column;
          min-width: 0;
          background: #0a0a0a;
          position: relative;
        }

        /* Top Bar */
        .topbar {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 18px 28px;
          border-bottom: 1px solid #141414;
          background: #0a0a0a;
          flex-shrink: 0;
        }

        .topbar-left {
          display: flex;
          align-items: center;
          gap: 14px;
        }

        .toggle-btn {
          background: none;
          border: 1px solid #1e1e1e;
          border-radius: 3px;
          color: #555;
          width: 30px;
          height: 30px;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 14px;
          transition: all 0.2s;
        }

        .toggle-btn:hover { border-color: #333; color: #aaa; }

        .topbar-title {
          font-family: 'Cormorant Garamond', serif;
          font-size: 18px;
          font-weight: 500;
          color: #e8e0d0;
        }

        .topbar-sub {
          font-size: 10px;
          color: #3a3a3a;
          letter-spacing: 0.1em;
        }

        .market-tickers {
          display: flex;
          gap: 20px;
        }

        .ticker {
          display: flex;
          flex-direction: column;
          align-items: flex-end;
          gap: 2px;
        }

        .ticker-name {
          font-size: 9px;
          letter-spacing: 0.1em;
          color: #3a3a3a;
          text-transform: uppercase;
        }

        .ticker-val {
          font-size: 12px;
          font-weight: 500;
          font-family: 'Cormorant Garamond', serif;
        }

        .ticker-val.pos { color: #6fcf97; }
        .ticker-val.neg { color: #eb5757; }

        /* Messages */
        .messages-area {
          flex: 1;
          overflow-y: auto;
          padding: 28px;
          display: flex;
          flex-direction: column;
          gap: 24px;
        }

        .messages-area::-webkit-scrollbar { width: 3px; }
        .messages-area::-webkit-scrollbar-track { background: transparent; }
        .messages-area::-webkit-scrollbar-thumb { background: #1e1e1e; border-radius: 2px; }

        .message-row {
          display: flex;
          flex-direction: column;
          animation: fadeSlideIn 0.3s ease forwards;
        }

        .message-row.user { align-items: flex-end; }
        .message-row.assistant { align-items: flex-start; }

        .message-meta {
          display: flex;
          align-items: center;
          gap: 8px;
          margin-bottom: 7px;
        }

        .message-row.user .message-meta { flex-direction: row-reverse; }

        .message-avatar {
          width: 26px;
          height: 26px;
          border-radius: 3px;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 10px;
          font-weight: 600;
          flex-shrink: 0;
        }

        .message-row.assistant .message-avatar {
          background: #c8a96e18;
          border: 1px solid #c8a96e30;
          color: #c8a96e;
          font-family: 'Cormorant Garamond', serif;
          font-size: 12px;
        }

        .message-row.user .message-avatar {
          background: #1e1e1e;
          border: 1px solid #2a2a2a;
          color: #888;
        }

        .message-sender {
          font-size: 10px;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          color: #3a3a3a;
        }

        .message-time {
          font-size: 9px;
          color: #2a2a2a;
        }

        .message-bubble {
          max-width: 68%;
          padding: 14px 18px;
          border-radius: 4px;
          font-size: 13px;
          line-height: 1.7;
          white-space: pre-line;
        }

        .message-row.assistant .message-bubble {
          background: #111;
          border: 1px solid #1a1a1a;
          border-top-left-radius: 1px;
          color: #ccc;
        }

        .message-row.user .message-bubble {
          background: #c8a96e12;
          border: 1px solid #c8a96e25;
          border-top-right-radius: 1px;
          color: #e8e0d0;
        }

        .typing-bubble {
          background: #111;
          border: 1px solid #1a1a1a;
          padding: 12px 18px;
          border-radius: 4px;
          border-top-left-radius: 1px;
          animation: fadeSlideIn 0.3s ease forwards;
        }

        /* Suggested pills */
        .suggestions {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          padding: 0 28px 14px;
          flex-shrink: 0;
        }

        .suggestion-pill {
          font-size: 11px;
          color: #666;
          border: 1px solid #1e1e1e;
          background: none;
          padding: 6px 12px;
          border-radius: 2px;
          cursor: pointer;
          transition: all 0.2s;
          font-family: 'DM Mono', monospace;
          letter-spacing: 0.02em;
        }

        .suggestion-pill:hover {
          border-color: #c8a96e40;
          color: #c8a96e;
          background: #c8a96e08;
        }

        /* Input bar */
        .input-bar {
          padding: 14px 28px 20px;
          flex-shrink: 0;
          border-top: 1px solid #141414;
        }

        .input-inner {
          display: flex;
          align-items: flex-end;
          gap: 10px;
          background: #0e0e0e;
          border: 1px solid #1e1e1e;
          border-radius: 5px;
          padding: 12px 14px;
          transition: border-color 0.2s;
        }

        .input-inner:focus-within {
          border-color: #c8a96e40;
        }

        .chat-input {
          flex: 1;
          background: none;
          border: none;
          outline: none;
          font-family: 'DM Mono', monospace;
          font-size: 13px;
          color: #e8e0d0;
          resize: none;
          line-height: 1.6;
          max-height: 120px;
          overflow-y: auto;
        }

        .chat-input::placeholder { color: #2e2e2e; }

        .chat-input::-webkit-scrollbar { width: 2px; }
        .chat-input::-webkit-scrollbar-thumb { background: #2a2a2a; }

        .send-btn {
          background: #c8a96e;
          border: none;
          border-radius: 3px;
          width: 34px;
          height: 34px;
          display: flex;
          align-items: center;
          justify-content: center;
          cursor: pointer;
          flex-shrink: 0;
          transition: all 0.2s;
          color: #0a0a0a;
        }

        .send-btn:hover { background: #d4b87e; transform: scale(1.03); }
        .send-btn:disabled { opacity: 0.35; cursor: default; transform: none; }

        .input-hint {
          font-size: 9px;
          color: #252525;
          text-align: right;
          margin-top: 7px;
          letter-spacing: 0.05em;
        }
      `}</style>

      <div className="app-shell">
        {/* SIDEBAR */}
        <aside className={`sidebar${sidebarOpen ? "" : " collapsed"}`}>
          <div className="user-block">
            <span className="user-greeting">Welcome back</span>
            <span className="user-name">Alex Chen</span>
            <span className="user-badge">Premium Investor</span>
          </div>

          <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
            <div className="section-label">Portfolios</div>
            <div className="portfolio-list">
              {PORTFOLIOS.map((p) => (
                <div
                  key={p.id}
                  className={`portfolio-card${activePortfolio === p.id ? " active" : ""}`}
                  onClick={() => setActivePortfolio(p.id === activePortfolio ? null : p.id)}
                >
                  <div className="portfolio-card-top">
                    <span className="portfolio-name">{p.name}</span>
                    <span className={`portfolio-change ${p.positive ? "pos" : "neg"}`}>{p.change}</span>
                  </div>
                  <div className="portfolio-value">{p.value}</div>
                  <div className="portfolio-assets">
                    {p.assets.map((a) => <span key={a} className="asset-chip">{a}</span>)}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="sidebar-total">
            <div className="sidebar-total-label">Total Net Worth</div>
            <div className="sidebar-total-value">$443,470</div>
          </div>
        </aside>

        {/* MAIN */}
        <main className="main">
          {/* Topbar */}
          <div className="topbar">
            <div className="topbar-left">
              <button className="toggle-btn" onClick={() => setSidebarOpen(!sidebarOpen)}>
                {sidebarOpen ? "←" : "→"}
              </button>
              <div>
                <div className="topbar-title">Investment Advisor</div>
                <div className="topbar-sub">AI · Real-time Markets · Mar 6, 2026</div>
              </div>
            </div>
            <div className="market-tickers">
              {[
                { name: "S&P 500", val: "5,842.31", cls: "pos" },
                { name: "Nasdaq", val: "18,405.22", cls: "pos" },
                { name: "BTC", val: "$87,230", cls: "neg" },
              ].map((t) => (
                <div key={t.name} className="ticker">
                  <span className="ticker-name">{t.name}</span>
                  <span className={`ticker-val ${t.cls}`}>{t.val}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Messages */}
          <div className="messages-area">
            {messages.map((msg) => (
              <div key={msg.id} className={`message-row ${msg.role}`}>
                <div className="message-meta">
                  <div className="message-avatar">
                    {msg.role === "assistant" ? "∆" : "AC"}
                  </div>
                  <span className="message-sender">
                    {msg.role === "assistant" ? "Advisor AI" : "You"}
                  </span>
                  <span className="message-time">{msg.time}</span>
                </div>
                <div className="message-bubble">{renderText(msg.text)}</div>
              </div>
            ))}

            {isTyping && (
              <div className="message-row assistant">
                <div className="message-meta">
                  <div className="message-avatar">∆</div>
                  <span className="message-sender">Advisor AI</span>
                </div>
                <div className="typing-bubble"><TypingDots /></div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {/* Suggestions */}
          {messages.length < 3 && (
            <div className="suggestions">
              {SUGGESTED.map((s) => (
                <button key={s} className="suggestion-pill" onClick={() => handleSend(s)}>
                  {s}
                </button>
              ))}
            </div>
          )}

          {/* Input */}
          <div className="input-bar">
            <div className="input-inner">
              <textarea
                ref={textareaRef}
                className="chat-input"
                placeholder="Ask about your portfolio, markets, or strategy…"
                value={input}
                onChange={(e) => {
                  setInput(e.target.value);
                  e.target.style.height = "auto";
                  e.target.style.height = Math.min(e.target.scrollHeight, 120) + "px";
                }}
                onKeyDown={handleKey}
                rows={1}
                disabled={isTyping}
              />
              <button
                className="send-btn"
                onClick={() => handleSend()}
                disabled={!input.trim() || isTyping}
                title="Send (Enter)"
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="22" y1="2" x2="11" y2="13" />
                  <polygon points="22 2 15 22 11 13 2 9 22 2" />
                </svg>
              </button>
            </div>
            <div className="input-hint">ENTER to send · SHIFT+ENTER for new line</div>
          </div>
        </main>
      </div>
    </>
  );
}
