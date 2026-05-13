/**
 * About Quala / Portfolio Optimizer — value proposition, product surface, and differentiation.
 * Rendered inside the top-bar info modal (not a separate route).
 */

const sectionTitle = {
  fontSize: 11,
  fontWeight: 700,
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color: "#c8a96e",
  marginBottom: 10,
  marginTop: 0,
};

const body = {
  fontSize: 13,
  color: "var(--text-muted)",
  lineHeight: 1.65,
  marginBottom: 14,
};

const list = {
  margin: "0 0 14px",
  paddingLeft: 18,
  fontSize: 13,
  color: "var(--text-muted)",
  lineHeight: 1.65,
};

const lead = {
  ...body,
  fontSize: 14,
  color: "var(--text)",
  marginBottom: 18,
};

function Bullet({ children }) {
  return <li style={{ marginBottom: 8 }}>{children}</li>;
}

export function AboutUsModalBody() {
  return (
    <div className="about-us-modal-body" style={{ maxHeight: "min(70vh, 560px)", overflowY: "auto", paddingRight: 4 }}>
      <p style={lead}>
        <strong style={{ color: "var(--text)" }}>Quala</strong> is a financial scenario-modeling workspace: you describe
        your situation, explore allocations, and stress-test outcomes with backtests and Monte Carlo—without handing us
        your brokerage login.
      </p>

      <h4 style={sectionTitle}>Value proposition</h4>
      <ul style={list}>
        <Bullet>
          <strong style={{ color: "var(--text)" }}>Range outcomes, not a single headline number.</strong> See how
          assumptions and portfolio tilts change paths under history and simulated futures.
        </Bullet>
        <Bullet>
          <strong style={{ color: "var(--text)" }}>Growth and retirement in one thread.</strong> Build accumulation
          portfolios, then connect them to decumulation planning when you are ready—explicitly, not as an afterthought.
        </Bullet>
        <Bullet>
          <strong style={{ color: "var(--text)" }}>Transparent inputs.</strong> Intake, saved portfolios, scenarios, and
          life plans carry the assumptions you used so you can revisit and compare versions over time.
        </Bullet>
      </ul>

      <h4 style={{ ...sectionTitle, marginTop: 20 }}>What you can do in this product</h4>
      <ul style={list}>
        <Bullet>
          <strong style={{ color: "var(--text)" }}>Guided intake and chat workflows</strong> for growth and retirement
          (Quala / Panda and related agents), including multi-option portfolio presentations and refinement loops.
        </Bullet>
        <Bullet>
          <strong style={{ color: "var(--text)" }}>Historical backtests and Monte Carlo</strong> on saved portfolios and
          analyze flows, with charts and narrative summaries where enabled.
        </Bullet>
        <Bullet>
          <strong style={{ color: "var(--text)" }}>Saved portfolios</strong> (growth and retirement categories), ticker
          weights, optional sector/industry views, mark-to-market valuation history, and composition updates.
        </Bullet>
        <Bullet>
          <strong style={{ color: "var(--text)" }}>Saved scenarios</strong> per portfolio—named intake snapshots for
          what-if exploration without overwriting your baseline.
        </Bullet>
        <Bullet>
          <strong style={{ color: "var(--text)" }}>Life planner</strong> pairs a growth item with a retirement item,
          runs the growth simulation, carries the median growth outcome into retirement starting wealth, then runs the
          retirement simulation; save the pair as one named life scenario.
        </Bullet>
        <Bullet>
          <strong style={{ color: "var(--text)" }}>Compare</strong> same-category portfolios or scenarios side by side,
          or open the life planner workspace from the sidebar.
        </Bullet>
        <Bullet>
          <strong style={{ color: "var(--text)" }}>Analyze current portfolio</strong> via CSV upload, column mapping,
          merged holdings, live weights, and optional agent backtest—then save like other portfolios.
        </Bullet>
        <Bullet>
          <strong style={{ color: "var(--text)" }}>Net worth</strong> workspace for assets, debts, and linked portfolio
          values (manual entry—no bank feed in this build).
        </Bullet>
        <Bullet>
          <strong style={{ color: "var(--text)" }}>Accounts and persistence</strong> with register/login, saved user
          intake, and session continuity for returning work.
        </Bullet>
      </ul>

      <h4 style={{ ...sectionTitle, marginTop: 20 }}>How we are different from typical apps</h4>
      <ul style={list}>
        <Bullet>
          <strong style={{ color: "var(--text)" }}>Scenario-first architecture.</strong> Many apps optimize a single
          plan today; Quala is built around portfolios, scenarios, and life bundles so you can keep alternatives
          alongside your baseline.
        </Bullet>
        <Bullet>
          <strong style={{ color: "var(--text)" }}>Explicit growth → retirement handoff.</strong> The life planner
          encodes a deliberate bridge from accumulation results to retirement starting conditions—stronger than a
          generic “retirement slider” disconnected from your growth assumptions.
        </Bullet>
        <Bullet>
          <strong style={{ color: "var(--text)" }}>No custody, no trading.</strong> We do not execute trades or sync
          bank/brokerage accounts in this product surface; you bring holdings and assumptions, which keeps scope clear
          and reduces surprise automation.
        </Bullet>
        <Bullet>
          <strong style={{ color: "var(--text)" }}>Conversation plus structure.</strong> Natural-language guidance sits
          on top of validated intake fields and saved artifacts, rather than replacing them—useful when you want both
          speed and auditability.
        </Bullet>
        <Bullet>
          <strong style={{ color: "var(--text)" }}>Model transparency and disclaimers.</strong> Outputs are framed as
          hypothetical scenario modeling with clear non-advice disclosures—appropriate for planning and education, not
          a substitute for a licensed professional when you need personalized advice.
        </Bullet>
      </ul>

      <p style={{ ...body, marginBottom: 0, fontSize: 12, color: "var(--text-dim)" }}>
        Product name in the UI may appear as Quala AI / Portfolio Optimizer depending on context; capabilities reflect
        the codebase you are running.
      </p>
    </div>
  );
}
