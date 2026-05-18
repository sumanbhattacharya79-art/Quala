"""
SQLite + sqlite-vec backend, or Postgres (Supabase) when DATABASE_URL is set.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import uuid
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Tuple, Union

try:
    import pysqlite3.dbapi2 as sqlite3  # type: ignore  # enables extension loading for sqlite-vec
except ImportError:
    import sqlite3

from backend.db_connection import PgConnectionAdapter, close_db_pool, get_pool, use_postgres

_log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "data" / "portfolio.db"


def _hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    """Hash password with salt. Returns (hash_hex, salt)."""
    s = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), s.encode("utf-8"), 100000)
    return h.hex(), s


def _verify_password(password: str, stored_hash: str, salt: str) -> bool:
    """Verify password against stored hash and salt."""
    h, _ = _hash_password(password, salt)
    return h == stored_hash


def _get_connection() -> sqlite3.Connection:
    """Return a connection with sqlite-vec loaded."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        conn.enable_load_extension(True)
        import sqlite_vec  # noqa: F401
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except ImportError:
        _log.warning("sqlite-vec not installed; running without vector support")
    except Exception as e:
        _log.warning("Could not load sqlite-vec: %s", e)
    return conn


@contextmanager
def get_db():
    """Context manager for database connections (SQLite file or Supabase Postgres pool)."""
    if use_postgres():
        pool = get_pool()
        with pool.connection() as conn:
            try:
                yield PgConnectionAdapter(conn)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return
    conn = _get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_columns(conn: Any) -> None:
    """Add portfolio_name and portfolio_value if missing (migration)."""
    try:
        conn.execute("ALTER TABLE saved_portfolios ADD COLUMN portfolio_name TEXT NOT NULL DEFAULT 'My Portfolio'")
    except sqlite3.OperationalError:
        pass  # column exists
    try:
        conn.execute("ALTER TABLE saved_portfolios ADD COLUMN portfolio_value REAL")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE user_intake ADD COLUMN inflation_assumption REAL")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE saved_portfolios ADD COLUMN portfolio_category TEXT DEFAULT 'growth'")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE saved_portfolios ADD COLUMN intake_json TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE saved_portfolios ADD COLUMN updated_at TEXT")
    except sqlite3.OperationalError:
        pass
    # analyzed_portfolios computed columns (from analyze_portfolio_parser.py)
    for col, col_type in (
        ("cost_basis_by_ticker_json", "TEXT"),
        ("quantity_by_ticker_json", "TEXT"),
        ("current_price_by_ticker_json", "TEXT"),
        ("current_amount_by_ticker_json", "TEXT"),
        ("weights_by_ticker_json", "TEXT"),
        ("total_portfolio_value", "REAL"),
    ):
        try:
            conn.execute(f"ALTER TABLE analyzed_portfolios ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute("ALTER TABLE net_worth_entries ADD COLUMN portfolio_id TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE saved_portfolios ADD COLUMN ticker_positions_json TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE saved_portfolios ADD COLUMN positions_as_of_date TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE portfolio_value_history ADD COLUMN holdings_json TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(
            "ALTER TABLE life_scenarios ADD COLUMN frozen_growth_median_at_retirement_usd REAL"
        )
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(
            "ALTER TABLE life_scenarios ADD COLUMN life_owns_growth_scenario INTEGER NOT NULL DEFAULT 1"
        )
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(
            "ALTER TABLE life_scenarios ADD COLUMN life_owns_retirement_scenario INTEGER NOT NULL DEFAULT 1"
        )
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(
            "ALTER TABLE life_scenarios ADD COLUMN retirement_success_percent REAL"
        )
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE life_scenarios ADD COLUMN growth_planner_intake_json TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE life_scenarios ADD COLUMN retirement_planner_intake_json TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute('ALTER TABLE user ADD COLUMN google_sub TEXT')
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE user ADD COLUMN auth_provider TEXT DEFAULT 'password'")
    except sqlite3.OperationalError:
        pass
    for col, ddl in (
        ("plan_tier", "TEXT NOT NULL DEFAULT 'free'"),
        ("stripe_customer_id", "TEXT"),
        ("stripe_subscription_id", "TEXT"),
        ("subscription_status", "TEXT"),
        ("plan_period_end", "TEXT"),
    ):
        try:
            conn.execute(f'ALTER TABLE user ADD COLUMN {col} {ddl}')
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute("""
            UPDATE net_worth_entries
            SET created_at = (
                SELECT p.created_at FROM saved_portfolios AS p
                WHERE p.portfolio_id = net_worth_entries.portfolio_id
                  AND p.user_id = net_worth_entries.user_id
            )
            WHERE portfolio_id IS NOT NULL
              AND TRIM(portfolio_id) != ''
              AND EXISTS (
                SELECT 1 FROM saved_portfolios AS p2
                WHERE p2.portfolio_id = net_worth_entries.portfolio_id
                  AND p2.user_id = net_worth_entries.user_id
              )
        """)
    except sqlite3.OperationalError:
        pass
    _migrate_portfolio_backtest_snapshots_scenario_id(conn)


def _scenario_id_storage_key(scenario_id: Optional[str]) -> str:
    """Empty string = portfolio-level snapshot; non-empty = saved_scenarios.scenario_id."""
    return (scenario_id or "").strip()


def _migrate_portfolio_backtest_snapshots_scenario_id(conn: sqlite3.Connection) -> None:
    """SQLite: add scenario_id and composite PK (portfolio_id, scenario_id)."""
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(portfolio_backtest_snapshots)").fetchall()}
    except sqlite3.OperationalError:
        return
    if not cols:
        return
    if "scenario_id" not in cols:
        try:
            conn.execute(
                "ALTER TABLE portfolio_backtest_snapshots ADD COLUMN scenario_id TEXT NOT NULL DEFAULT ''"
            )
        except sqlite3.OperationalError:
            pass
    pk_cols = [
        r[1]
        for r in conn.execute("PRAGMA table_info(portfolio_backtest_snapshots)").fetchall()
        if r[5]
    ]
    if "scenario_id" in pk_cols:
        return
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_backtest_snapshots_v2 (
                portfolio_id TEXT NOT NULL,
                scenario_id TEXT NOT NULL DEFAULT '',
                user_id TEXT NOT NULL,
                run_kind TEXT NOT NULL,
                artifact_json TEXT NOT NULL,
                summary_metrics_json TEXT,
                intake_json TEXT,
                data_date_range TEXT,
                portfolio_weights_json TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (portfolio_id, scenario_id),
                FOREIGN KEY (portfolio_id) REFERENCES saved_portfolios(portfolio_id)
            )
        """)
        conn.execute("""
            INSERT INTO portfolio_backtest_snapshots_v2 (
                portfolio_id, scenario_id, user_id, run_kind, artifact_json,
                summary_metrics_json, intake_json, data_date_range, portfolio_weights_json,
                created_at, updated_at
            )
            SELECT
                portfolio_id, COALESCE(scenario_id, ''), user_id, run_kind, artifact_json,
                summary_metrics_json, intake_json, data_date_range, portfolio_weights_json,
                created_at, updated_at
            FROM portfolio_backtest_snapshots
        """)
        conn.execute("DROP TABLE portfolio_backtest_snapshots")
        conn.execute(
            "ALTER TABLE portfolio_backtest_snapshots_v2 RENAME TO portfolio_backtest_snapshots"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pbs_user_id ON portfolio_backtest_snapshots(user_id)"
        )
    except sqlite3.OperationalError:
        pass


def init_db() -> None:
    """Create tables if they do not exist (SQLite). With DATABASE_URL, schema is managed separately; only ping DB."""
    if use_postgres():
        pool = get_pool()
        with pool.connection() as c:
            c.execute("SELECT 1")
        return
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user (
                user_id TEXT PRIMARY KEY,
                email_id TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_user_email ON user(email_id)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS saved_portfolios (
                portfolio_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                portfolio_name TEXT NOT NULL DEFAULT 'My Portfolio',
                portfolio_value REAL,
                portfolio_ticker_weights TEXT NOT NULL,
                portfolio_sector_weights TEXT,
                portfolio_industry_weights TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_saved_portfolios_user_id ON saved_portfolios(user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_saved_portfolios_session_id ON saved_portfolios(session_id)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_intake (
                user_id TEXT PRIMARY KEY,
                initial_value REAL DEFAULT 1.0,
                monthly_savings REAL DEFAULT 0.0,
                horizon_years INTEGER,
                planning_for TEXT DEFAULT 'self',
                birth_dates TEXT,
                current_monthly_expense REAL DEFAULT 0.0,
                upcoming_expenses TEXT,
                display_unit TEXT,
                retirement_status TEXT,
                retirement_timeline_self TEXT,
                retirement_timeline_partner TEXT,
                country TEXT,
                state TEXT,
                risk TEXT,
                spending TEXT,
                other_notes TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES user(user_id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_intake_user_id ON user_intake(user_id)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS saved_scenarios (
                scenario_id TEXT PRIMARY KEY,
                portfolio_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                scenario_name TEXT NOT NULL,
                portfolio_name TEXT NOT NULL,
                description TEXT,
                intake_json TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (portfolio_id) REFERENCES saved_portfolios(portfolio_id),
                FOREIGN KEY (user_id) REFERENCES user(user_id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_saved_scenarios_portfolio_id ON saved_scenarios(portfolio_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_saved_scenarios_user_id ON saved_scenarios(user_id)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS life_scenarios (
                life_scenario_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                growth_scenario_id TEXT NOT NULL,
                retirement_scenario_id TEXT NOT NULL,
                frozen_growth_median_at_retirement_usd REAL,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES user(user_id),
                FOREIGN KEY (growth_scenario_id) REFERENCES saved_scenarios(scenario_id),
                FOREIGN KEY (retirement_scenario_id) REFERENCES saved_scenarios(scenario_id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_life_scenarios_user_id ON life_scenarios(user_id)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analyzed_portfolios (
                portfolio_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                holdings_json TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES user(user_id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_analyzed_portfolios_user_id ON analyzed_portfolios(user_id)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_net_worth (
                user_id TEXT PRIMARY KEY,
                data_json TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES user(user_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS net_worth_entries (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('asset', 'debt')),
                label TEXT NOT NULL DEFAULT '',
                value REAL NOT NULL DEFAULT 0,
                yoy_pct REAL NOT NULL DEFAULT 0,
                portfolio_id TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES user(user_id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_net_worth_entries_user_id ON net_worth_entries(user_id)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS net_worth_value_history (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
                kind TEXT NOT NULL CHECK (kind IN ('asset', 'debt')),
                name TEXT NOT NULL DEFAULT '',
                value REAL NOT NULL DEFAULT 0,
                portfolio_id TEXT,
                FOREIGN KEY (user_id) REFERENCES user(user_id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_nwvh_user_time ON net_worth_value_history(user_id, recorded_at)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_value_history (
                portfolio_id TEXT NOT NULL,
                as_of_date TEXT NOT NULL,
                value REAL NOT NULL,
                PRIMARY KEY (portfolio_id, as_of_date),
                FOREIGN KEY (portfolio_id) REFERENCES saved_portfolios(portfolio_id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pvh_portfolio ON portfolio_value_history(portfolio_id)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_backtest_snapshots (
                portfolio_id TEXT NOT NULL,
                scenario_id TEXT NOT NULL DEFAULT '',
                user_id TEXT NOT NULL,
                run_kind TEXT NOT NULL,
                artifact_json TEXT NOT NULL,
                summary_metrics_json TEXT,
                intake_json TEXT,
                data_date_range TEXT,
                portfolio_weights_json TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (portfolio_id, scenario_id),
                FOREIGN KEY (portfolio_id) REFERENCES saved_portfolios(portfolio_id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pbs_user_id ON portfolio_backtest_snapshots(user_id)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_backtest_monthly_history (
                portfolio_id TEXT NOT NULL,
                year_month TEXT NOT NULL,
                run_kind TEXT NOT NULL,
                artifact_json TEXT NOT NULL,
                summary_metrics_json TEXT,
                data_date_range TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (portfolio_id, year_month),
                FOREIGN KEY (portfolio_id) REFERENCES saved_portfolios(portfolio_id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pbmh_portfolio ON portfolio_backtest_monthly_history(portfolio_id)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_gemini_token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                source TEXT NOT NULL,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES user(user_id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_gemini_token_usage_user ON user_gemini_token_usage(user_id)"
        )
        _ensure_columns(conn)


def record_user_gemini_token_usage(
    user_id: str,
    *,
    source: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
) -> None:
    """Append one Gemini usage row for a logged-in user (CrewAI or generate_content paths)."""
    uid = (user_id or "").strip()
    if not uid:
        return
    src = ((source or "unknown").strip() or "unknown")[:120]
    try:
        pt = max(0, int(prompt_tokens))
        ct = max(0, int(completion_tokens))
        tt = max(0, int(total_tokens))
    except (TypeError, ValueError):
        return
    if pt == 0 and ct == 0 and tt == 0:
        return
    init_db()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO user_gemini_token_usage
            (user_id, source, prompt_tokens, completion_tokens, total_tokens)
            VALUES (?, ?, ?, ?, ?)
            """,
            (uid, src, pt, ct, tt),
        )


def create_user(email_id: str, password: str) -> str:
    """
    Create a new user. Returns user_id.
    Raises ValueError if email already exists.
    """
    init_db()
    with get_db() as conn:
        existing = conn.execute(
            "SELECT user_id FROM user WHERE email_id = ?",
            (email_id.strip().lower(),),
        ).fetchone()
        if existing:
            raise ValueError("Email already registered")
        user_id = str(uuid.uuid4())
        pwd_hash, pwd_salt = _hash_password(password)
        conn.execute(
            """
            INSERT INTO user (user_id, email_id, password_hash, password_salt, auth_provider)
            VALUES (?, ?, ?, ?, 'password')
            """,
            (user_id, email_id.strip().lower(), pwd_hash, pwd_salt),
        )
    return user_id


def get_user_by_email(email_id: str) -> Optional[Dict[str, Any]]:
    """Fetch user by email. Returns dict with user_id, email_id, password_hash, password_salt."""
    init_db()
    with get_db() as conn:
        row = conn.execute(
            "SELECT user_id, email_id, password_hash, password_salt FROM user WHERE email_id = ?",
            (email_id.strip().lower(),),
        ).fetchone()
    if not row:
        return None
    return {
        "user_id": row["user_id"],
        "email_id": row["email_id"],
        "password_hash": row["password_hash"],
        "password_salt": row["password_salt"],
    }


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    init_db()
    with get_db() as conn:
        row = conn.execute(
            "SELECT user_id, email_id FROM user WHERE user_id = ?",
            (user_id.strip(),),
        ).fetchone()
    if not row:
        return None
    return {"user_id": row["user_id"], "email_id": row["email_id"]}


def _user_billing_row_to_dict(row: Any) -> Dict[str, Any]:
    keys = row.keys() if hasattr(row, "keys") else ()
    return {
        "user_id": row["user_id"],
        "email_id": row["email_id"] if "email_id" in keys else None,
        "plan_tier": (row["plan_tier"] if "plan_tier" in keys else None) or "free",
        "stripe_customer_id": row["stripe_customer_id"] if "stripe_customer_id" in keys else None,
        "stripe_subscription_id": row["stripe_subscription_id"] if "stripe_subscription_id" in keys else None,
        "subscription_status": row["subscription_status"] if "subscription_status" in keys else None,
        "plan_period_end": row["plan_period_end"] if "plan_period_end" in keys else None,
    }


def get_user_billing(user_id: str) -> Optional[Dict[str, Any]]:
    init_db()
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT user_id, email_id, plan_tier, stripe_customer_id,
                   stripe_subscription_id, subscription_status, plan_period_end
            FROM user WHERE user_id = ?
            """,
            (user_id.strip(),),
        ).fetchone()
    if not row:
        return None
    return _user_billing_row_to_dict(row)


def get_user_id_by_stripe_customer(stripe_customer_id: str) -> Optional[str]:
    cid = (stripe_customer_id or "").strip()
    if not cid:
        return None
    init_db()
    with get_db() as conn:
        row = conn.execute(
            "SELECT user_id FROM user WHERE stripe_customer_id = ?",
            (cid,),
        ).fetchone()
    return str(row["user_id"]) if row else None


def update_user_stripe_billing(
    *,
    user_id: str,
    plan_tier: str,
    stripe_customer_id: Optional[str] = None,
    stripe_subscription_id: Optional[str] = None,
    subscription_status: Optional[str] = None,
    plan_period_end: Optional[str] = None,
) -> None:
    init_db()
    with get_db() as conn:
        conn.execute(
            """
            UPDATE user SET
                plan_tier = ?,
                stripe_customer_id = COALESCE(?, stripe_customer_id),
                stripe_subscription_id = ?,
                subscription_status = ?,
                plan_period_end = ?
            WHERE user_id = ?
            """,
            (
                (plan_tier or "free").strip().lower(),
                stripe_customer_id,
                stripe_subscription_id,
                subscription_status,
                plan_period_end,
                user_id.strip(),
            ),
        )


def verify_user(email_id: str, password: str) -> Optional[str]:
    """Verify credentials. Returns user_id if valid, else None."""
    user = get_user_by_email(email_id)
    if not user:
        return None
    pwd_hash = user.get("password_hash")
    pwd_salt = user.get("password_salt")
    if not pwd_hash or not pwd_salt:
        return None
    if not _verify_password(password, pwd_hash, pwd_salt):
        return None
    return user["user_id"]


def get_user_by_google_sub(google_sub: str) -> Optional[Dict[str, Any]]:
    """Lookup user by Google subject id."""
    if not google_sub:
        return None
    init_db()
    with get_db() as conn:
        row = conn.execute(
            "SELECT user_id, email_id, auth_provider FROM user WHERE google_sub = ?",
            (google_sub,),
        ).fetchone()
    if not row:
        return None
    return {
        "user_id": row["user_id"],
        "email_id": row["email_id"],
        "auth_provider": row["auth_provider"] if "auth_provider" in row.keys() else "google",
    }


def create_user_from_google(*, google_sub: str, email_id: str) -> str:
    """Create a Google-only user (no password)."""
    init_db()
    email = email_id.strip().lower()
    with get_db() as conn:
        existing = conn.execute(
            "SELECT user_id FROM user WHERE email_id = ?",
            (email,),
        ).fetchone()
        if existing:
            raise ValueError(
                "An account with this email already exists. Sign in with email and password."
            )
        user_id = str(uuid.uuid4())
        if use_postgres():
            conn.execute(
                """
                INSERT INTO user (user_id, email_id, password_hash, password_salt, google_sub, auth_provider)
                VALUES (?, ?, NULL, NULL, ?, 'google')
                """,
                (user_id, email, google_sub),
            )
        else:
            conn.execute(
                """
                INSERT INTO user (user_id, email_id, password_hash, password_salt, google_sub, auth_provider)
                VALUES (?, ?, '', '', ?, 'google')
                """,
                (user_id, email, google_sub),
            )
    return user_id


def resolve_google_auth_user(
    *, google_sub: str, email_id: str, email_verified: bool
) -> tuple[str, Literal["existing", "new"]]:
    """
    Returns (user_id, status). status is 'new' when the caller must create the user
    (after terms acceptance). Raises ValueError on policy violations.
    """
    if not email_verified:
        raise ValueError("Google email not verified")
    if not google_sub:
        raise ValueError("Invalid Google sign-in")
    email = email_id.strip().lower()
    if not email:
        raise ValueError("Google account has no email")

    hit = get_user_by_google_sub(google_sub)
    if hit:
        return hit["user_id"], "existing"

    by_email = get_user_by_email(email)
    if by_email:
        raise ValueError(
            "An account with this email already exists. Sign in with email and password."
        )

    return "", "new"


def get_or_create_user_from_google(
    *, google_sub: str, email_id: str, email_verified: bool
) -> tuple[str, bool]:
    """Returns (user_id, created_new_user)."""
    user_id, status = resolve_google_auth_user(
        google_sub=google_sub, email_id=email_id, email_verified=email_verified
    )
    if status == "existing":
        return user_id, False
    user_id = create_user_from_google(google_sub=google_sub, email_id=email_id)
    return user_id, True


def portfolio_name_exists_for_user(
    user_id: str,
    portfolio_name: str,
    exclude_portfolio_id: Optional[str] = None,
) -> bool:
    """
    True if this user already has a saved portfolio with the same name (case-insensitive, trimmed).
    exclude_portfolio_id: when updating that row by id, omit it from the check.
    """
    name = (portfolio_name or "").strip() or "My Portfolio"
    init_db()
    q = """
        SELECT 1 FROM saved_portfolios
        WHERE user_id = ? AND LOWER(TRIM(portfolio_name)) = LOWER(?)
    """
    params: List[Any] = [user_id, name]
    if exclude_portfolio_id:
        q += " AND portfolio_id != ?"
        params.append(exclude_portfolio_id)
    with get_db() as conn:
        row = conn.execute(q, params).fetchone()
    return row is not None


def _intake_dict_from_row_column(row: Union[sqlite3.Row, Mapping[str, Any]], col: str = "intake_json") -> Optional[Dict[str, Any]]:
    if col not in row.keys():
        return None
    raw = row[col]
    if not raw:
        return None
    try:
        out = json.loads(raw)
        if isinstance(out, dict):
            from backend.intake_parser import coalesce_intake_spending_only

            coalesce_intake_spending_only(out)
        return out if isinstance(out, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def save_portfolio(
    user_id: str,
    session_id: str,
    ticker_weights: Dict[str, float],
    sector_weights: Optional[Dict[str, float]] = None,
    industry_weights: Optional[Dict[str, float]] = None,
    portfolio_id: Optional[str] = None,
    portfolio_name: str = "My Portfolio",
    portfolio_value: Optional[float] = None,
    portfolio_category: str = "growth",
    intake_snapshot: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Save a portfolio to the database. Returns the portfolio_id.
    portfolio_category: "growth" (Quala) or "retirement" (Panda).
    intake_snapshot: JSON-serializable intake dict attached to this portfolio only.
    """
    init_db()
    pid = portfolio_id or str(uuid.uuid4())
    pname = (portfolio_name or "My Portfolio").strip() or "My Portfolio"
    if portfolio_name_exists_for_user(user_id, pname, exclude_portfolio_id=pid):
        raise ValueError("Name already taken, try a different one.")
    ticker_json = json.dumps(ticker_weights)
    sector_json = json.dumps(sector_weights) if sector_weights else None
    industry_json = json.dumps(industry_weights) if industry_weights else None
    category = portfolio_category if portfolio_category in ("growth", "retirement") else "growth"
    intake_json = json.dumps(intake_snapshot) if intake_snapshot else None

    with get_db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO saved_portfolios
            (portfolio_id, user_id, session_id, portfolio_name, portfolio_value,
             portfolio_ticker_weights, portfolio_sector_weights, portfolio_industry_weights, portfolio_category, intake_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (pid, user_id, session_id, pname, portfolio_value,
             ticker_json, sector_json, industry_json, category, intake_json),
        )
    return pid


def update_portfolio_intake_snapshot(portfolio_id: str, user_id: str, intake: Dict[str, Any]) -> bool:
    """Update only the attached intake JSON for a saved portfolio (owner must match)."""
    init_db()
    payload = json.dumps(intake)
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE saved_portfolios SET intake_json = ? WHERE portfolio_id = ? AND user_id = ?",
            (payload, portfolio_id, user_id),
        )
        return cur.rowcount > 0


def update_portfolio_ticker_weights(
    portfolio_id: str,
    user_id: str,
    ticker_weights: Dict[str, float],
) -> bool:
    """Replace ticker weights for a saved portfolio; clears sector/industry (stale). Owner must match."""
    init_db()
    tw: Dict[str, float] = {}
    for k, v in ticker_weights.items():
        if v is None or k is None:
            continue
        key = str(k).strip().upper()
        if not key:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv <= 0:
            continue
        tw[key] = tw.get(key, 0.0) + fv
    total = sum(tw.values())
    if total <= 0 or not tw:
        return False
    tw = {k: v / total for k, v in tw.items()}
    payload = json.dumps(tw)
    with get_db() as conn:
        cur = conn.execute(
            """
            UPDATE saved_portfolios SET
                portfolio_ticker_weights = ?,
                portfolio_sector_weights = NULL,
                portfolio_industry_weights = NULL,
                updated_at = datetime('now')
            WHERE portfolio_id = ? AND user_id = ?
            """,
            (payload, portfolio_id, user_id),
        )
        return cur.rowcount > 0


def save_user_intake(
    user_id: str,
    *,
    initial_value: float = 1.0,
    monthly_savings: float = 0.0,
    horizon_years: Optional[int] = None,
    planning_for: str = "self",
    birth_dates: Optional[List[Dict[str, Any]]] = None,
    current_monthly_expense: float = 0.0,
    upcoming_expenses: Optional[List[Dict[str, Any]]] = None,
    display_unit: Optional[str] = None,
    retirement_status: Optional[str] = None,
    retirement_timeline_self: Optional[str] = None,
    retirement_timeline_partner: Optional[str] = None,
    country: Optional[str] = None,
    state: Optional[str] = None,
    inflation_assumption: Optional[float] = None,
    risk: Optional[str] = None,
    spending: Optional[str] = None,
    other_notes: Optional[str] = None,
) -> None:
    """
    Save or update user intake. Called when user has signed up and submits intake
    (e.g. when saving a portfolio). Upserts by user_id.
    """
    init_db()
    birth_json = json.dumps(birth_dates) if birth_dates else None
    ue_json = json.dumps(upcoming_expenses) if upcoming_expenses else None

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO user_intake (
                user_id, initial_value, monthly_savings, horizon_years, planning_for,
                birth_dates, current_monthly_expense, upcoming_expenses, display_unit,
                retirement_status, retirement_timeline_self, retirement_timeline_partner,
                country, state, inflation_assumption, risk, spending, other_notes, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(user_id) DO UPDATE SET
                initial_value = excluded.initial_value,
                monthly_savings = excluded.monthly_savings,
                horizon_years = excluded.horizon_years,
                planning_for = excluded.planning_for,
                birth_dates = excluded.birth_dates,
                current_monthly_expense = excluded.current_monthly_expense,
                upcoming_expenses = excluded.upcoming_expenses,
                display_unit = excluded.display_unit,
                retirement_status = excluded.retirement_status,
                retirement_timeline_self = excluded.retirement_timeline_self,
                retirement_timeline_partner = excluded.retirement_timeline_partner,
                country = excluded.country,
                state = excluded.state,
                inflation_assumption = excluded.inflation_assumption,
                risk = excluded.risk,
                spending = excluded.spending,
                other_notes = excluded.other_notes,
                updated_at = datetime('now')
            """,
            (
                user_id,
                initial_value,
                monthly_savings,
                horizon_years,
                planning_for or "self",
                birth_json,
                current_monthly_expense,
                ue_json,
                display_unit,
                retirement_status,
                retirement_timeline_self,
                retirement_timeline_partner,
                country,
                state,
                inflation_assumption,
                risk,
                spending,
                other_notes,
            ),
        )


def get_user_intake(user_id: str) -> Optional[Dict[str, Any]]:
    """Fetch user intake by user_id."""
    init_db()
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM user_intake WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return None
    result: Dict[str, Any] = {
        "user_id": row["user_id"],
        "initial_value": row["initial_value"] if row["initial_value"] is not None else 1.0,
        "monthly_savings": row["monthly_savings"] if row["monthly_savings"] is not None else 0.0,
        "horizon_years": row["horizon_years"],
        "planning_for": row["planning_for"] or "self",
        "current_monthly_expense": row["current_monthly_expense"] or 0.0,
        "display_unit": row["display_unit"],
        "retirement_status": row["retirement_status"],
        "retirement_timeline_self": row["retirement_timeline_self"],
        "retirement_timeline_partner": row["retirement_timeline_partner"],
        "country": row["country"],
        "state": row["state"],
        "inflation_assumption": row["inflation_assumption"] if "inflation_assumption" in row.keys() and row["inflation_assumption"] is not None else 3.0,
        "risk": row["risk"],
        "spending": row["spending"],
        "other_notes": row["other_notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if row["birth_dates"]:
        try:
            result["birth_dates"] = json.loads(row["birth_dates"])
        except (json.JSONDecodeError, TypeError):
            result["birth_dates"] = None
    else:
        result["birth_dates"] = None
    legacy_ue: List[Dict[str, Any]] = []
    if row["upcoming_expenses"]:
        try:
            raw_ue = json.loads(row["upcoming_expenses"])
            if isinstance(raw_ue, list):
                legacy_ue = [x for x in raw_ue if isinstance(x, dict)]
        except (json.JSONDecodeError, TypeError):
            legacy_ue = []
    if legacy_ue:
        result["upcoming_expenses"] = legacy_ue
    return result


def get_portfolio(portfolio_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a single portfolio by id."""
    init_db()
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM saved_portfolios WHERE portfolio_id = ?",
            (portfolio_id,),
        ).fetchone()
    if not row:
        return None
    intake = _intake_dict_from_row_column(row)
    return {
        "portfolio_id": row["portfolio_id"],
        "user_id": row["user_id"],
        "session_id": row["session_id"],
        "portfolio_name": row["portfolio_name"] if "portfolio_name" in row.keys() else "My Portfolio",
        "portfolio_value": row["portfolio_value"] if "portfolio_value" in row.keys() else None,
        "portfolio_ticker_weights": json.loads(row["portfolio_ticker_weights"]),
        "portfolio_sector_weights": (
            json.loads(row["portfolio_sector_weights"])
            if row["portfolio_sector_weights"] else None
        ),
        "portfolio_industry_weights": (
            json.loads(row["portfolio_industry_weights"])
            if row["portfolio_industry_weights"] else None
        ),
        "portfolio_category": (row["portfolio_category"] if "portfolio_category" in row.keys() else None) or "growth",
        "created_at": row["created_at"],
        "updated_at": (
            row["updated_at"]
            if "updated_at" in row.keys() and row["updated_at"]
            else row["created_at"]
        ),
        "intake": intake,
        "portfolio_ticker_positions": (
            json.loads(row["ticker_positions_json"])
            if "ticker_positions_json" in row.keys() and row["ticker_positions_json"]
            else None
        ),
        "positions_as_of_date": (
            row["positions_as_of_date"] if "positions_as_of_date" in row.keys() else None
        ),
    }


def set_portfolio_positions(portfolio_id: str, positions: Dict[str, Any], as_of_date: str) -> None:
    init_db()
    payload = json.dumps(positions)
    with get_db() as conn:
        conn.execute(
            """
            UPDATE saved_portfolios
            SET ticker_positions_json = ?, positions_as_of_date = ?, updated_at = datetime('now')
            WHERE portfolio_id = ?
            """,
            (payload, as_of_date[:10], portfolio_id),
        )


def update_portfolio_market_value(portfolio_id: str, value: float) -> None:
    init_db()
    with get_db() as conn:
        conn.execute(
            """
            UPDATE saved_portfolios
            SET portfolio_value = ?, updated_at = datetime('now')
            WHERE portfolio_id = ?
            """,
            (float(value), portfolio_id),
        )


def replace_portfolio_value_history(
    portfolio_id: str,
    rows: List[Tuple[Any, ...]],
) -> None:
    init_db()
    with get_db() as conn:
        conn.execute("DELETE FROM portfolio_value_history WHERE portfolio_id = ?", (portfolio_id,))
        for row in rows:
            if len(row) >= 3 and isinstance(row[2], dict):
                d, v, hold = row[0], row[1], row[2]
                hj = json.dumps(hold, sort_keys=True, separators=(",", ":")) if hold else None
            else:
                d, v = row[0], row[1]
                hj = None
            conn.execute(
                """
                INSERT OR REPLACE INTO portfolio_value_history (portfolio_id, as_of_date, value, holdings_json)
                VALUES (?, ?, ?, ?)
                """,
                (portfolio_id, str(d)[:10], float(v), hj),
            )


def upsert_portfolio_value_row(
    portfolio_id: str,
    as_of_date: str,
    value: float,
    holdings: Optional[Dict[str, float]] = None,
) -> None:
    init_db()
    hj = (
        json.dumps(holdings, sort_keys=True, separators=(",", ":"))
        if holdings
        else None
    )
    with get_db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO portfolio_value_history (portfolio_id, as_of_date, value, holdings_json)
            VALUES (?, ?, ?, ?)
            """,
            (portfolio_id, str(as_of_date)[:10], float(value), hj),
        )


def get_portfolio_value_history(portfolio_id: str) -> List[Dict[str, Any]]:
    init_db()
    with get_db() as conn:
        cur = conn.execute(
            """
            SELECT as_of_date, value, holdings_json FROM portfolio_value_history
            WHERE portfolio_id = ? ORDER BY as_of_date ASC
            """,
            (portfolio_id,),
        )
        out: List[Dict[str, Any]] = []
        for r in cur.fetchall():
            item: Dict[str, Any] = {
                "date": r["as_of_date"],
                "value": float(r["value"]),
            }
            raw_h = r["holdings_json"] if "holdings_json" in r.keys() else None
            if raw_h:
                try:
                    parsed = json.loads(raw_h)
                    if isinstance(parsed, dict) and parsed:
                        item["by_ticker"] = {
                            str(k).upper(): float(v)
                            for k, v in parsed.items()
                            if v is not None and float(v) > 0
                        }
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
            out.append(item)
        return out


def upsert_portfolio_backtest_snapshot(
    portfolio_id: str,
    user_id: str,
    run_kind: str,
    artifact_json: str,
    summary_metrics_json: Optional[str],
    intake_json: Optional[str],
    data_date_range: Optional[str],
    portfolio_weights_json: Optional[str],
    updated_at: str,
    scenario_id: Optional[str] = None,
) -> None:
    init_db()
    sid = _scenario_id_storage_key(scenario_id)
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO portfolio_backtest_snapshots (
                portfolio_id, scenario_id, user_id, run_kind, artifact_json, summary_metrics_json,
                intake_json, data_date_range, portfolio_weights_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(portfolio_id, scenario_id) DO UPDATE SET
                user_id = excluded.user_id,
                run_kind = excluded.run_kind,
                artifact_json = excluded.artifact_json,
                summary_metrics_json = excluded.summary_metrics_json,
                intake_json = excluded.intake_json,
                data_date_range = excluded.data_date_range,
                portfolio_weights_json = excluded.portfolio_weights_json,
                updated_at = excluded.updated_at
            """,
            (
                portfolio_id,
                sid,
                user_id,
                run_kind,
                artifact_json,
                summary_metrics_json,
                intake_json,
                data_date_range,
                portfolio_weights_json,
                updated_at,
                updated_at,
            ),
        )


def get_portfolio_backtest_snapshot(
    portfolio_id: str,
    scenario_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    init_db()
    sid = _scenario_id_storage_key(scenario_id)
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT * FROM portfolio_backtest_snapshots
            WHERE portfolio_id = ? AND scenario_id = ?
            """,
            (portfolio_id, sid),
        ).fetchone()
    return dict(row) if row else None


def copy_portfolio_backtest_snapshot_to_scenario(
    portfolio_id: str,
    user_id: str,
    scenario_id: str,
    *,
    from_scenario_id: Optional[str] = None,
) -> bool:
    """Copy persisted backtest row to a scenario key (e.g. after Save as scenario)."""
    src_sid = _scenario_id_storage_key(from_scenario_id)
    dst_sid = _scenario_id_storage_key(scenario_id)
    if not dst_sid:
        return False
    row = get_portfolio_backtest_snapshot(portfolio_id, src_sid)
    if not row or row.get("user_id") != user_id:
        return False
    upsert_portfolio_backtest_snapshot(
        portfolio_id,
        user_id,
        str(row.get("run_kind") or "growth"),
        str(row.get("artifact_json") or ""),
        row.get("summary_metrics_json"),
        row.get("intake_json"),
        row.get("data_date_range"),
        row.get("portfolio_weights_json"),
        str(row.get("updated_at") or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")),
        scenario_id=dst_sid,
    )
    return True


def upsert_portfolio_backtest_monthly_history(
    portfolio_id: str,
    year_month: str,
    run_kind: str,
    artifact_json: str,
    summary_metrics_json: Optional[str],
    data_date_range: Optional[str],
    created_at: str,
) -> None:
    init_db()
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO portfolio_backtest_monthly_history (
                portfolio_id, year_month, run_kind, artifact_json,
                summary_metrics_json, data_date_range, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(portfolio_id, year_month) DO UPDATE SET
                run_kind = excluded.run_kind,
                artifact_json = excluded.artifact_json,
                summary_metrics_json = excluded.summary_metrics_json,
                data_date_range = excluded.data_date_range,
                created_at = excluded.created_at
            """,
            (
                portfolio_id,
                year_month,
                run_kind,
                artifact_json,
                summary_metrics_json,
                data_date_range,
                created_at,
            ),
        )


def delete_portfolio_backtest_data(portfolio_id: str) -> None:
    init_db()
    with get_db() as conn:
        conn.execute(
            "DELETE FROM portfolio_backtest_monthly_history WHERE portfolio_id = ?",
            (portfolio_id,),
        )
        conn.execute(
            "DELETE FROM portfolio_backtest_snapshots WHERE portfolio_id = ?",
            (portfolio_id,),
        )


def delete_scenario_backtest_snapshot(scenario_id: str) -> None:
    init_db()
    sid = _scenario_id_storage_key(scenario_id)
    if not sid:
        return
    with get_db() as conn:
        conn.execute(
            "DELETE FROM portfolio_backtest_snapshots WHERE scenario_id = ?",
            (sid,),
        )


def list_all_saved_portfolios(limit: int = 5000) -> List[Dict[str, Any]]:
    """All saved portfolios (for batch jobs)."""
    init_db()
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT portfolio_id, user_id, portfolio_category, portfolio_ticker_weights, intake_json
            FROM saved_portfolios
            ORDER BY COALESCE(updated_at, created_at) DESC, created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        raw = d.get("portfolio_ticker_weights")
        if isinstance(raw, str):
            try:
                d["portfolio_ticker_weights"] = json.loads(raw)
            except json.JSONDecodeError:
                d["portfolio_ticker_weights"] = {}
        raw_i = d.get("intake_json")
        if isinstance(raw_i, str):
            try:
                d["intake"] = json.loads(raw_i)
            except json.JSONDecodeError:
                d["intake"] = None
        else:
            d["intake"] = raw_i
        d.pop("intake_json", None)
        out.append(d)
    return out


def list_portfolios(
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """List saved portfolios, optionally filtered by user_id or session_id."""
    init_db()
    query = "SELECT * FROM saved_portfolios WHERE 1=1"
    params: List[Any] = []
    if user_id:
        query += " AND user_id = ?"
        params.append(user_id)
    if session_id:
        query += " AND session_id = ?"
        params.append(session_id)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()

    return [
        {
            "portfolio_id": r["portfolio_id"],
            "user_id": r["user_id"],
            "session_id": r["session_id"],
            "portfolio_name": r["portfolio_name"] if "portfolio_name" in r.keys() else "My Portfolio",
            "portfolio_value": r["portfolio_value"] if "portfolio_value" in r.keys() else None,
            "portfolio_ticker_weights": json.loads(r["portfolio_ticker_weights"]),
            "portfolio_sector_weights": (
                json.loads(r["portfolio_sector_weights"])
                if r["portfolio_sector_weights"] else None
            ),
            "portfolio_industry_weights": (
                json.loads(r["portfolio_industry_weights"])
                if r["portfolio_industry_weights"] else None
            ),
            "portfolio_category": (r["portfolio_category"] if "portfolio_category" in r.keys() else None) or "growth",
            "created_at": r["created_at"],
            "updated_at": (
                r["updated_at"]
                if "updated_at" in r.keys() and r["updated_at"]
                else r["created_at"]
            ),
            "intake": _intake_dict_from_row_column(r),
            "portfolio_ticker_positions": (
                json.loads(r["ticker_positions_json"])
                if "ticker_positions_json" in r.keys() and r["ticker_positions_json"]
                else None
            ),
            "positions_as_of_date": (
                r["positions_as_of_date"] if "positions_as_of_date" in r.keys() else None
            ),
        }
        for r in rows
    ]


def count_portfolios_for_user(user_id: str) -> int:
    """Number of saved portfolios for this user."""
    init_db()
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM saved_portfolios WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return int(row["c"]) if row else 0


def count_scenarios_for_user(user_id: str) -> int:
    init_db()
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM saved_scenarios WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return int(row["c"]) if row else 0


def scenario_name_exists_for_user(
    user_id: str,
    scenario_name: str,
    exclude_scenario_id: Optional[str] = None,
) -> bool:
    """
    True if this user already has a scenario with the same name (case-insensitive, trimmed).
    exclude_scenario_id: when updating that row by id, omit it from the check.
    """
    name = (scenario_name or "").strip()
    if not name:
        return False
    init_db()
    q = """
        SELECT 1 FROM saved_scenarios
        WHERE user_id = ? AND LOWER(TRIM(scenario_name)) = LOWER(?)
    """
    params: List[Any] = [user_id, name]
    if exclude_scenario_id:
        q += " AND scenario_id != ?"
        params.append(exclude_scenario_id)
    with get_db() as conn:
        row = conn.execute(q, params).fetchone()
    return row is not None


def save_scenario(
    portfolio_id: str,
    user_id: str,
    scenario_name: str,
    portfolio_name: str,
    intake: Dict[str, Any],
    description: Optional[str] = None,
) -> str:
    """Save a scenario. Returns scenario_id."""
    init_db()
    sname = (scenario_name or "").strip()
    if scenario_name_exists_for_user(user_id, sname):
        raise ValueError("Name already taken, try a different one.")
    sid = str(uuid.uuid4())
    intake_json = json.dumps(intake)
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO saved_scenarios
            (scenario_id, portfolio_id, user_id, scenario_name, portfolio_name, description, intake_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (sid, portfolio_id, user_id, sname, portfolio_name, description or None, intake_json),
        )
    return sid


def _portfolio_slug(portfolio_name: str) -> str:
    name = (portfolio_name or "").strip() or "My Portfolio"
    slug = re.sub(r"[^\w\-]", "-", name.lower()).replace("--", "-").strip("-") or "portfolio"
    return slug


def _life_bundle_derived_scenario_name(life_nm: str, side: str, portfolio_name: str) -> str:
    """
    Default scenario display name when saving a life bundle (new rows only).
    Avoids doubling prefixes like "retire-retire-1" when the portfolio slug already starts with "retire-".
    """
    slug = _portfolio_slug(portfolio_name)
    if side == "growth":
        if slug == "growth" or slug.startswith("growth-"):
            return f"{life_nm} — {slug}"
        return f"{life_nm} — growth-{slug}"
    if slug == "retire" or slug.startswith("retire-"):
        return f"{life_nm} — {slug}"
    return f"{life_nm} — retire-{slug}"


def count_life_scenarios_for_user(user_id: str) -> int:
    """Number of saved life plans for this user (product limit: at most one)."""
    init_db()
    with get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM life_scenarios WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return int(row["c"]) if row and row["c"] is not None else 0


def life_scenario_name_exists_for_user(user_id: str, name: str) -> bool:
    nm = (name or "").strip()
    if not nm:
        return False
    init_db()
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM life_scenarios
            WHERE user_id = ? AND LOWER(TRIM(name)) = LOWER(?)
            """,
            (user_id, nm),
        ).fetchone()
    return row is not None


def _baseline_intake_dict_from_portfolio(portfolio_row: Dict[str, Any]) -> Dict[str, Any]:
    """Portfolio snapshot intake for new saved_scenarios rows; life planner edits live on life_scenarios only."""
    w = portfolio_row.get("intake")
    return dict(w) if isinstance(w, dict) else {}


def _scenario_referenced_by_any_life(user_id: str, scenario_id: str) -> bool:
    """True if any life_scenarios row already uses this scenario on either side."""
    sid = (scenario_id or "").strip()
    if not sid:
        return False
    init_db()
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM life_scenarios
            WHERE user_id = ? AND (growth_scenario_id = ? OR retirement_scenario_id = ?)
            """,
            (user_id, sid, sid),
        ).fetchone()
    return row is not None


def _auto_link_scenario_id_for_new_life_bundle(user_id: str, portfolio_id: str) -> Optional[str]:
    """
    When a portfolio has exactly one saved scenario, return its id so a new life bundle can link
    without INSERTing a duplicate — unless that scenario is already used as growth or retirement
    on *any* life plan for this user. Otherwise a second save (portfolio-only drops, same
    portfolios) would point both life rows at the same pair even when the user intended separate
    plans. Library scenarios with life_owns=0 still conflict if another life plan already references them.
    """
    init_db()
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT scenario_id FROM saved_scenarios
            WHERE user_id = ? AND portfolio_id = ?
            ORDER BY created_at ASC
            """,
            (user_id, portfolio_id),
        ).fetchall()
    if len(rows) != 1:
        return None
    sid = str(rows[0]["scenario_id"])
    with get_db() as conn:
        conflict = conn.execute(
            """
            SELECT 1 FROM life_scenarios
            WHERE user_id = ?
              AND (growth_scenario_id = ? OR retirement_scenario_id = ?)
            """,
            (user_id, sid, sid),
        ).fetchone()
    return None if conflict else sid


def save_life_scenario_bundle(
    user_id: str,
    life_name: str,
    growth_portfolio_id: str,
    retirement_portfolio_id: str,
    growth_intake: Dict[str, Any],
    retirement_intake: Dict[str, Any],
    description: Optional[str] = None,
    frozen_growth_median_at_retirement_usd: Optional[float] = None,
    retirement_success_percent: Optional[float] = None,
    growth_scenario_id: Optional[str] = None,
    retirement_scenario_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Atomically save growth + retirement scenarios and link them as one life scenario.
    Life-planner-specific intakes (including what-if) are stored on ``life_scenarios`` only
    (``growth_planner_intake_json`` / ``retirement_planner_intake_json``); linked or new
    ``saved_scenarios`` rows are never overwritten with planner edits—new scenario rows use
    the portfolio snapshot intake as ``intake_json`` so portfolio/scenario baselines stay stable.
    If an id is omitted but that portfolio has exactly one saved scenario that no life plan uses
    yet, that row is linked without inserting a duplicate. If the sole scenario is already linked
    from another life row, a new ``saved_scenarios`` row is inserted for this bundle instead.
    Client-supplied scenario ids that are already used by another life plan are ignored so each
    new life row can get its own pair when the user saves a second plan on the same portfolios.
    New scenarios use display names derived from the life name and portfolio slug, avoiding
    redundant prefixes (e.g. "retire-retire-1" when the portfolio is "retire 1").
    """
    init_db()
    life_nm = (life_name or "").strip()
    if not life_nm:
        raise ValueError("Life scenario name is required.")
    if count_life_scenarios_for_user(user_id) >= 1:
        raise ValueError(
            "Only one life plan can be saved. Delete your current life plan in Life planner, then save again."
        )
    if life_scenario_name_exists_for_user(user_id, life_nm):
        raise ValueError("That life scenario name is already in use. Try a different name.")

    g_row = get_portfolio(growth_portfolio_id)
    r_row = get_portfolio(retirement_portfolio_id)
    if not g_row or g_row.get("user_id") != user_id:
        raise ValueError("Growth portfolio not found or not authorized.")
    if not r_row or r_row.get("user_id") != user_id:
        raise ValueError("Retirement portfolio not found or not authorized.")
    if (g_row.get("portfolio_category") or "growth") != "growth":
        raise ValueError("Left portfolio must be a growth portfolio.")
    if (r_row.get("portfolio_category") or "growth") != "retirement":
        raise ValueError("Right portfolio must be a retirement portfolio.")

    g_pf_name = (g_row.get("portfolio_name") or "My Portfolio").strip()
    r_pf_name = (r_row.get("portfolio_name") or "My Portfolio").strip()
    sname_g = _life_bundle_derived_scenario_name(life_nm, "growth", g_pf_name)
    sname_r = _life_bundle_derived_scenario_name(life_nm, "retirement", r_pf_name)

    gsid = (growth_scenario_id or "").strip() or None
    rsid = (retirement_scenario_id or "").strip() or None
    # Portfolio-only drops omit scenario ids; reuse the sole scenario only when it is not another
    # life's owned snapshot (see _auto_link_scenario_id_for_new_life_bundle).
    if not gsid:
        gsid = _auto_link_scenario_id_for_new_life_bundle(user_id, growth_portfolio_id)
    if not rsid:
        rsid = _auto_link_scenario_id_for_new_life_bundle(user_id, retirement_portfolio_id)
    # Drag-and-drop usually sends explicit scenario ids; those still reuse rows unless we drop them
    # when another life plan already references the same scenario (otherwise life 2 == life 1).
    if gsid and _scenario_referenced_by_any_life(user_id, gsid):
        gsid = None
        gsid = _auto_link_scenario_id_for_new_life_bundle(user_id, growth_portfolio_id)
    if rsid and _scenario_referenced_by_any_life(user_id, rsid):
        rsid = None
        rsid = _auto_link_scenario_id_for_new_life_bundle(user_id, retirement_portfolio_id)

    owns_growth = True
    owns_retirement = True
    gid: str
    rid: str
    planner_g_json = json.dumps(growth_intake)
    planner_r_json = json.dumps(retirement_intake)

    if gsid:
        gs = get_scenario(gsid)
        if not gs or gs.get("user_id") != user_id or gs.get("portfolio_id") != growth_portfolio_id:
            raise ValueError("Growth scenario not found or does not match the growth portfolio.")
        gid = gsid
        owns_growth = False
        sname_g = (gs.get("scenario_name") or sname_g).strip() or sname_g
    else:
        if scenario_name_exists_for_user(user_id, sname_g):
            raise ValueError(
                "A scenario with the derived growth name already exists. Try a different life scenario name."
            )
        gid = str(uuid.uuid4())

    if rsid:
        rs = get_scenario(rsid)
        if not rs or rs.get("user_id") != user_id or rs.get("portfolio_id") != retirement_portfolio_id:
            raise ValueError("Retirement scenario not found or does not match the retirement portfolio.")
        rid = rsid
        owns_retirement = False
        sname_r = (rs.get("scenario_name") or sname_r).strip() or sname_r
    else:
        if scenario_name_exists_for_user(user_id, sname_r):
            raise ValueError(
                "A scenario with the derived retirement name already exists. Try a different life scenario name."
            )
        rid = str(uuid.uuid4())

    frozen_usd: Optional[float] = None
    if frozen_growth_median_at_retirement_usd is not None:
        try:
            fv = float(frozen_growth_median_at_retirement_usd)
            if fv == fv and fv > 0:
                frozen_usd = fv
        except (TypeError, ValueError):
            frozen_usd = None

    retire_pct: Optional[float] = None
    if retirement_success_percent is not None:
        try:
            rv = float(retirement_success_percent)
            if rv == rv:
                retire_pct = max(0.0, min(100.0, rv))
        except (TypeError, ValueError):
            retire_pct = None

    g_snap = json.dumps(_baseline_intake_dict_from_portfolio(g_row))
    r_snap = json.dumps(_baseline_intake_dict_from_portfolio(r_row))
    with get_db() as conn:
        if owns_growth:
            conn.execute(
                """
                INSERT INTO saved_scenarios
                (scenario_id, portfolio_id, user_id, scenario_name, portfolio_name, description, intake_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (gid, growth_portfolio_id, user_id, sname_g, g_pf_name, description, g_snap),
            )
        if owns_retirement:
            conn.execute(
                """
                INSERT INTO saved_scenarios
                (scenario_id, portfolio_id, user_id, scenario_name, portfolio_name, description, intake_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (rid, retirement_portfolio_id, user_id, sname_r, r_pf_name, description, r_snap),
            )
        lid = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO life_scenarios
            (life_scenario_id, user_id, name, growth_scenario_id, retirement_scenario_id,
             frozen_growth_median_at_retirement_usd, retirement_success_percent,
             life_owns_growth_scenario, life_owns_retirement_scenario,
             growth_planner_intake_json, retirement_planner_intake_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lid,
                user_id,
                life_nm,
                gid,
                rid,
                frozen_usd,
                retire_pct,
                1 if owns_growth else 0,
                1 if owns_retirement else 0,
                planner_g_json,
                planner_r_json,
            ),
        )

    return {
        "life_scenario_id": lid,
        "name": life_nm,
        "growth_scenario_id": gid,
        "retirement_scenario_id": rid,
        "growth_scenario_name": sname_g,
        "retirement_scenario_name": sname_r,
        "frozen_growth_median_at_retirement_usd": frozen_usd,
        "retirement_success_percent": retire_pct,
    }


def update_life_scenario_planner_intakes(
    life_scenario_id: str,
    user_id: str,
    growth_intake: Dict[str, Any],
    retirement_intake: Dict[str, Any],
    name: Optional[str] = None,
) -> bool:
    """Persist life-planner-only intakes without modifying linked saved_scenarios or portfolios."""
    init_db()
    with get_db() as conn:
        chk = conn.execute(
            "SELECT 1 FROM life_scenarios WHERE life_scenario_id = ? AND user_id = ?",
            (life_scenario_id, user_id),
        ).fetchone()
    if not chk:
        return False
    gij = json.dumps(growth_intake)
    rij = json.dumps(retirement_intake)
    nm = (name or "").strip()
    with get_db() as conn:
        if nm:
            cur = conn.execute(
                """
                UPDATE life_scenarios
                SET growth_planner_intake_json = ?, retirement_planner_intake_json = ?, name = ?
                WHERE life_scenario_id = ? AND user_id = ?
                """,
                (gij, rij, nm, life_scenario_id, user_id),
            )
        else:
            cur = conn.execute(
                """
                UPDATE life_scenarios
                SET growth_planner_intake_json = ?, retirement_planner_intake_json = ?
                WHERE life_scenario_id = ? AND user_id = ?
                """,
                (gij, rij, life_scenario_id, user_id),
            )
    return cur.rowcount > 0


def update_life_scenario_frozen_growth_median(
    life_scenario_id: str,
    user_id: str,
    frozen_growth_median_at_retirement_usd: Optional[float] = None,
    retirement_success_percent: Optional[float] = None,
) -> bool:
    """Persist frozen growth median and/or retirement success snapshot after backtests."""
    frozen_usd: Optional[float] = None
    if frozen_growth_median_at_retirement_usd is not None:
        try:
            fv = float(frozen_growth_median_at_retirement_usd)
            if fv == fv and fv > 0:
                frozen_usd = fv
        except (TypeError, ValueError):
            frozen_usd = None

    retire_pct: Optional[float] = None
    if retirement_success_percent is not None:
        try:
            rv = float(retirement_success_percent)
            if rv == rv:
                retire_pct = max(0.0, min(100.0, rv))
        except (TypeError, ValueError):
            retire_pct = None

    sets: List[str] = []
    params: List[Any] = []
    if frozen_growth_median_at_retirement_usd is not None:
        sets.append("frozen_growth_median_at_retirement_usd = ?")
        params.append(frozen_usd)
    if retirement_success_percent is not None:
        sets.append("retirement_success_percent = ?")
        params.append(retire_pct)

    if not sets:
        return True

    init_db()
    params.extend([life_scenario_id, user_id])
    with get_db() as conn:
        cur = conn.execute(
            f"""
            UPDATE life_scenarios
            SET {", ".join(sets)}
            WHERE life_scenario_id = ? AND user_id = ?
            """,
            tuple(params),
        )
        return cur.rowcount > 0


def list_life_scenarios_by_user(user_id: str) -> List[Dict[str, Any]]:
    init_db()
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT l.life_scenario_id, l.user_id, l.name, l.growth_scenario_id, l.retirement_scenario_id,
                   l.frozen_growth_median_at_retirement_usd, l.retirement_success_percent, l.created_at,
                   l.life_owns_growth_scenario, l.life_owns_retirement_scenario,
                   g.portfolio_id AS growth_portfolio_id,
                   r.portfolio_id AS retirement_portfolio_id
            FROM life_scenarios l
            INNER JOIN saved_scenarios g
              ON g.scenario_id = l.growth_scenario_id AND g.user_id = l.user_id
            INNER JOIN saved_scenarios r
              ON r.scenario_id = l.retirement_scenario_id AND r.user_id = l.user_id
            WHERE l.user_id = ?
            ORDER BY l.created_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_life_scenario_for_user(life_scenario_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    init_db()
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM life_scenarios
            WHERE life_scenario_id = ? AND user_id = ?
            """,
            (life_scenario_id, user_id),
        ).fetchone()
    if not row:
        return None
    meta = dict(row)
    for raw_k, out_k in (
        ("growth_planner_intake_json", "growth_planner_intake"),
        ("retirement_planner_intake_json", "retirement_planner_intake"),
    ):
        raw = meta.pop(raw_k, None)
        if raw:
            try:
                meta[out_k] = json.loads(raw)
            except Exception:
                meta[out_k] = None
        else:
            meta[out_k] = None
    if "life_owns_growth_scenario" not in meta or meta["life_owns_growth_scenario"] is None:
        meta["life_owns_growth_scenario"] = 1
    if "life_owns_retirement_scenario" not in meta or meta["life_owns_retirement_scenario"] is None:
        meta["life_owns_retirement_scenario"] = 1
    g = get_scenario(meta["growth_scenario_id"])
    r = get_scenario(meta["retirement_scenario_id"])
    if not g or not r or g.get("user_id") != user_id or r.get("user_id") != user_id:
        return None
    gp = get_portfolio(g.get("portfolio_id") or "")
    rp = get_portfolio(r.get("portfolio_id") or "")
    if gp and gp.get("user_id") == user_id:
        pn = (gp.get("portfolio_name") or "").strip()
        if pn:
            g = {**g, "portfolio_name": pn}
    if rp and rp.get("user_id") == user_id:
        pn = (rp.get("portfolio_name") or "").strip()
        if pn:
            r = {**r, "portfolio_name": pn}
    return {**meta, "growth": g, "retirement": r}


def delete_life_scenario_for_user(life_scenario_id: str, user_id: str) -> bool:
    """Remove a life scenario row. Deletes linked saved_scenarios only for sides this bundle created."""
    init_db()
    row = get_life_scenario_for_user(life_scenario_id, user_id)
    if not row:
        return False
    gid = row["growth_scenario_id"]
    rid = row["retirement_scenario_id"]
    own_g = int(row.get("life_owns_growth_scenario") or 0) == 1
    own_r = int(row.get("life_owns_retirement_scenario") or 0) == 1
    with get_db() as conn:
        conn.execute(
            "DELETE FROM life_scenarios WHERE life_scenario_id = ? AND user_id = ?",
            (life_scenario_id, user_id),
        )
        if own_g:
            conn.execute(
                "DELETE FROM saved_scenarios WHERE user_id = ? AND scenario_id = ?",
                (user_id, gid),
            )
        if own_r:
            conn.execute(
                "DELETE FROM saved_scenarios WHERE user_id = ? AND scenario_id = ?",
                (user_id, rid),
            )
    if own_g:
        delete_scenario_backtest_snapshot(gid)
    if own_r:
        delete_scenario_backtest_snapshot(rid)
    return True


def get_scenarios_by_user(user_id: str) -> List[Dict[str, Any]]:
    """List all saved scenarios for a user, ordered by portfolio then created_at."""
    init_db()
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT scenario_id, portfolio_id, user_id, scenario_name, portfolio_name,
                   description, intake_json, created_at
            FROM saved_scenarios
            WHERE user_id = ?
            ORDER BY portfolio_id, created_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [
        {
            "scenario_id": r["scenario_id"],
            "portfolio_id": r["portfolio_id"],
            "user_id": r["user_id"],
            "scenario_name": r["scenario_name"],
            "portfolio_name": r["portfolio_name"],
            "description": r["description"],
            "intake": json.loads(r["intake_json"]) if r["intake_json"] else {},
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def get_scenario(scenario_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a single scenario by id."""
    init_db()
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM saved_scenarios WHERE scenario_id = ?",
            (scenario_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "scenario_id": row["scenario_id"],
        "portfolio_id": row["portfolio_id"],
        "user_id": row["user_id"],
        "scenario_name": row["scenario_name"],
        "portfolio_name": row["portfolio_name"],
        "description": row["description"],
        "intake": json.loads(row["intake_json"]) if row["intake_json"] else {},
        "created_at": row["created_at"],
    }


def delete_scenario(scenario_id: str, user_id: str) -> bool:
    """Delete one scenario if it belongs to user_id. Returns True if a row was deleted."""
    init_db()
    with get_db() as conn:
        conn.execute(
            """
            DELETE FROM life_scenarios
            WHERE user_id = ? AND (growth_scenario_id = ? OR retirement_scenario_id = ?)
            """,
            (user_id, scenario_id, scenario_id),
        )
        cur = conn.execute(
            "DELETE FROM saved_scenarios WHERE scenario_id = ? AND user_id = ?",
            (scenario_id, user_id),
        )
        deleted = cur.rowcount > 0
    if deleted:
        delete_scenario_backtest_snapshot(scenario_id)
    return deleted


def update_scenario(
    scenario_id: str,
    user_id: str,
    intake: Dict[str, Any],
    description: Optional[str] = None,
) -> bool:
    """Update scenario intake and optionally description. Returns True if updated."""
    init_db()
    row = get_scenario(scenario_id)
    if not row or row["user_id"] != user_id:
        return False
    intake_json = json.dumps(intake)
    with get_db() as conn:
        conn.execute(
            """
            UPDATE saved_scenarios
            SET intake_json = ?, description = ?
            WHERE scenario_id = ? AND user_id = ?
            """,
            (intake_json, description or row.get("description"), scenario_id, user_id),
        )
    return True


def save_analyzed_portfolio(user_id: str, holdings: List[Dict[str, Any]]) -> str:
    """Save analyzed portfolio (CSV rows). Returns portfolio_id."""
    init_db()
    portfolio_id = str(uuid.uuid4())
    holdings_json = json.dumps(holdings)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO analyzed_portfolios (portfolio_id, user_id, holdings_json) VALUES (?, ?, ?)",
            (portfolio_id, user_id, holdings_json),
        )
    return portfolio_id


def list_analyzed_portfolios(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """List analyzed portfolios, optionally filtered by user_id."""
    init_db()
    with get_db() as conn:
        if user_id:
            rows = conn.execute(
                "SELECT * FROM analyzed_portfolios WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM analyzed_portfolios ORDER BY created_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]


def get_analyzed_portfolio_owner_user_id(portfolio_id: str) -> Optional[str]:
    """Return owning user_id for an analyzed_portfolios row, or None if missing."""
    init_db()
    with get_db() as conn:
        row = conn.execute(
            "SELECT user_id FROM analyzed_portfolios WHERE portfolio_id = ?",
            (portfolio_id,),
        ).fetchone()
    if not row:
        return None
    return str(row["user_id"])


def delete_analyzed_portfolio(portfolio_id: str, user_id: str) -> bool:
    """Delete one analyzed CSV snapshot row if it belongs to user_id. Returns True if a row was removed."""
    init_db()
    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM analyzed_portfolios WHERE portfolio_id = ? AND user_id = ?",
            (portfolio_id, user_id),
        )
        return cur.rowcount > 0


def update_analyzed_portfolio_computed(
    portfolio_id: str,
    cost_basis_by_ticker: Dict[str, float],
    quantity_by_ticker: Dict[str, float],
    current_price_by_ticker: Dict[str, float],
    current_amount_by_ticker: Dict[str, float],
    weights_by_ticker: Dict[str, float],
    total_portfolio_value: float,
) -> bool:
    """Update analyzed_portfolios row with computed JSON fields."""
    init_db()
    with get_db() as conn:
        cur = conn.execute(
            """UPDATE analyzed_portfolios SET
                cost_basis_by_ticker_json = ?,
                quantity_by_ticker_json = ?,
                current_price_by_ticker_json = ?,
                current_amount_by_ticker_json = ?,
                weights_by_ticker_json = ?,
                total_portfolio_value = ?
            WHERE portfolio_id = ?""",
            (
                json.dumps(cost_basis_by_ticker),
                json.dumps(quantity_by_ticker),
                json.dumps(current_price_by_ticker),
                json.dumps(current_amount_by_ticker),
                json.dumps(weights_by_ticker),
                total_portfolio_value,
                portfolio_id,
            ),
        )
        return cur.rowcount > 0


def delete_saved_portfolio(portfolio_id: str, user_id: str) -> bool:
    """Delete one saved portfolio if it belongs to user_id. Returns True if a row was deleted.

    Also removes ``portfolio_value_history`` (portfolio value chart), unlinks net worth rows,
    and drops ``net_worth_value_history`` snapshots for this portfolio (net worth over time chart).
    """
    init_db()
    _unlink_portfolio_from_user_net_worth(user_id, portfolio_id)
    with get_db() as conn:
        conn.execute("DELETE FROM portfolio_value_history WHERE portfolio_id = ?", (portfolio_id,))
        conn.execute(
            "DELETE FROM portfolio_backtest_monthly_history WHERE portfolio_id = ?",
            (portfolio_id,),
        )
        conn.execute(
            "DELETE FROM portfolio_backtest_snapshots WHERE portfolio_id = ?",
            (portfolio_id,),
        )
        conn.execute(
            """
            DELETE FROM life_scenarios
            WHERE user_id = ? AND (
                growth_scenario_id IN (
                    SELECT scenario_id FROM saved_scenarios WHERE portfolio_id = ? AND user_id = ?
                )
                OR retirement_scenario_id IN (
                    SELECT scenario_id FROM saved_scenarios WHERE portfolio_id = ? AND user_id = ?
                )
            )
            """,
            (user_id, portfolio_id, user_id, portfolio_id, user_id),
        )
        conn.execute("DELETE FROM saved_scenarios WHERE portfolio_id = ? AND user_id = ?", (portfolio_id, user_id))
        cur = conn.execute(
            "DELETE FROM saved_portfolios WHERE portfolio_id = ? AND user_id = ?",
            (portfolio_id, user_id),
        )
        return cur.rowcount > 0


def delete_portfolios_by_user(user_id: str) -> int:
    """Delete all saved portfolios for a user. Returns count deleted."""
    init_db()
    with get_db() as conn:
        conn.execute(
            """
            DELETE FROM portfolio_value_history
            WHERE portfolio_id IN (SELECT portfolio_id FROM saved_portfolios WHERE user_id = ?)
            """,
            (user_id,),
        )
        conn.execute(
            """
            DELETE FROM portfolio_backtest_monthly_history
            WHERE portfolio_id IN (SELECT portfolio_id FROM saved_portfolios WHERE user_id = ?)
            """,
            (user_id,),
        )
        conn.execute(
            """
            DELETE FROM portfolio_backtest_snapshots
            WHERE portfolio_id IN (SELECT portfolio_id FROM saved_portfolios WHERE user_id = ?)
            """,
            (user_id,),
        )
        conn.execute("DELETE FROM life_scenarios WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM saved_scenarios WHERE user_id = ?", (user_id,))
        cur = conn.execute("DELETE FROM saved_portfolios WHERE user_id = ?", (user_id,))
        return cur.rowcount


def delete_user_intake(user_id: str) -> bool:
    """Delete user intake for a user. Returns True if deleted."""
    init_db()
    with get_db() as conn:
        cur = conn.execute("DELETE FROM user_intake WHERE user_id = ?", (user_id,))
        return cur.rowcount > 0


def delete_user(user_id: str) -> bool:
    """Delete user and all their data (portfolios, intake). Returns True if deleted."""
    init_db()
    delete_portfolios_by_user(user_id)
    delete_user_intake(user_id)
    with get_db() as conn:
        conn.execute("DELETE FROM analyzed_portfolios WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM net_worth_entries WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM net_worth_value_history WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_net_worth WHERE user_id = ?", (user_id,))
        cur = conn.execute("DELETE FROM user WHERE user_id = ?", (user_id,))
        return cur.rowcount > 0


def _filter_linked_portfolio_ids_for_user(user_id: str, ids: List[str]) -> List[str]:
    """Keep only ids that exist in saved_portfolios and belong to user_id."""
    out: List[str] = []
    seen: set[str] = set()
    for raw in ids:
        pid_s = str(raw).strip()
        if not pid_s or pid_s in seen:
            continue
        row = get_portfolio(pid_s)
        if not row or row.get("user_id") != user_id:
            continue
        seen.add(pid_s)
        out.append(pid_s)
    return out


def _prune_net_worth_buckets_for_valid_portfolios(
    user_id: str, buckets: Dict[str, List[Dict[str, Any]]]
) -> Dict[str, List[Dict[str, Any]]]:
    """Drop linked asset rows whose portfolio_id no longer exists (e.g. portfolio deleted)."""
    assets_out: List[Dict[str, Any]] = []
    for a in buckets.get("assets", []):
        pid = a.get("portfolio_id")
        if pid:
            row = get_portfolio(str(pid).strip())
            if not row or row.get("user_id") != user_id:
                continue
        assets_out.append(a)
    return {"assets": assets_out, "debts": buckets.get("debts", [])}


def _unlink_portfolio_from_user_net_worth(user_id: str, portfolio_id: str) -> None:
    """Remove net worth entry rows and JSON links that reference a saved portfolio being deleted."""
    pid_s = str(portfolio_id).strip()
    if not pid_s:
        return
    init_db()
    with get_db() as conn:
        conn.execute(
            "DELETE FROM net_worth_entries WHERE user_id = ? AND portfolio_id = ?",
            (user_id, pid_s),
        )
        conn.execute(
            "DELETE FROM net_worth_value_history WHERE user_id = ? AND portfolio_id = ?",
            (user_id, pid_s),
        )
    row = _legacy_user_net_worth_row(user_id)
    if not row or not row["data_json"]:
        return
    try:
        data = json.loads(row["data_json"])
    except (json.JSONDecodeError, TypeError):
        return
    if not isinstance(data, dict):
        return
    linked = data.get("linked_portfolio_ids")
    if not isinstance(linked, list):
        return
    new_linked = [str(x) for x in linked if x and str(x).strip() != pid_s]
    with get_db() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM net_worth_entries WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    cnt = int(n["c"]) if n else 0
    if cnt == 0 and not new_linked:
        with get_db() as conn:
            conn.execute("DELETE FROM user_net_worth WHERE user_id = ?", (user_id,))
        return
    upsert_user_net_worth_links(user_id, new_linked)


def _legacy_user_net_worth_row(user_id: str) -> Optional[Any]:
    init_db()
    with get_db() as conn:
        return conn.execute(
            "SELECT data_json FROM user_net_worth WHERE user_id = ?",
            (user_id,),
        ).fetchone()


def _migrate_legacy_net_worth_blob(user_id: str) -> None:
    """One-time: copy assets/debts from legacy data_json into net_worth_entries."""
    row = _legacy_user_net_worth_row(user_id)
    if not row or not row["data_json"]:
        return
    try:
        data = json.loads(row["data_json"])
    except (json.JSONDecodeError, TypeError):
        return
    if not isinstance(data, dict):
        return
    assets = data.get("assets") if isinstance(data.get("assets"), list) else []
    debts = data.get("debts") if isinstance(data.get("debts"), list) else []
    linked = data.get("linked_portfolio_ids")
    lid = [str(x) for x in linked if x] if isinstance(linked, list) else []
    lid = _filter_linked_portfolio_ids_for_user(user_id, lid)
    if not assets and not debts and not lid:
        return
    replace_net_worth_entries(
        user_id, assets, debts, linked_portfolio_ids=lid, record_value_history=False
    )
    upsert_user_net_worth_links(user_id, lid)


def list_net_worth_entries(user_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """Rows from net_worth_entries; each item includes id, portfolio_id (if linked investment), label, etc."""
    init_db()
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, kind, label, value, yoy_pct, portfolio_id, created_at, updated_at
            FROM net_worth_entries
            WHERE user_id = ?
            ORDER BY kind ASC, created_at ASC, id ASC
            """,
            (user_id,),
        ).fetchall()
    assets: List[Dict[str, Any]] = []
    debts: List[Dict[str, Any]] = []
    for r in rows:
        kind = str(r["kind"] or "").strip() or "asset"
        pid = r["portfolio_id"] if "portfolio_id" in r.keys() else None
        portfolio_id = str(pid).strip() if pid else None
        item = {
            "id": r["id"],
            "user_id": user_id,
            "entry_kind": kind,
            "label": r["label"] or "",
            "price": float(r["value"] or 0),
            "value": float(r["value"] or 0),
            "yoy_pct": float(r["yoy_pct"] or 0),
            "portfolio_id": portfolio_id if portfolio_id else None,
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        if kind == "asset":
            assets.append(item)
        else:
            debts.append(item)
    return {"assets": assets, "debts": debts}


def get_user_net_worth_linked_portfolio_ids(user_id: str) -> List[str]:
    """Linked ids from JSON blob plus any asset rows tagged with portfolio_id."""
    init_db()
    from_entries: List[str] = []
    with get_db() as conn:
        erows = conn.execute(
            """
            SELECT DISTINCT portfolio_id FROM net_worth_entries
            WHERE user_id = ? AND portfolio_id IS NOT NULL AND TRIM(portfolio_id) != ''
            """,
            (user_id,),
        ).fetchall()
        from_entries = [str(r["portfolio_id"]) for r in erows if r.get("portfolio_id")]
        row = conn.execute(
            "SELECT data_json FROM user_net_worth WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    from_json: List[str] = []
    if row and row["data_json"]:
        try:
            data = json.loads(row["data_json"])
            if isinstance(data, dict):
                linked = data.get("linked_portfolio_ids")
                if isinstance(linked, list):
                    from_json = [str(x) for x in linked if x]
        except (json.JSONDecodeError, TypeError):
            pass
    merged = sorted(set(from_entries) | set(from_json))
    return _filter_linked_portfolio_ids_for_user(user_id, merged)


def upsert_user_net_worth_links(user_id: str, linked_portfolio_ids: List[str]) -> None:
    """Store only linked portfolio ids in user_net_worth.data_json (assets/debts live in net_worth_entries)."""
    init_db()
    payload = json.dumps({"linked_portfolio_ids": linked_portfolio_ids})
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO user_net_worth (user_id, data_json, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(user_id) DO UPDATE SET
                data_json = excluded.data_json,
                updated_at = datetime('now')
            """,
            (user_id, payload),
        )


def _insert_net_worth_value_history_row(
    conn: Any,
    user_id: str,
    kind: str,
    name: str,
    value: float,
    portfolio_id: Optional[str] = None,
) -> None:
    """Append one audit row (same connection as net worth replace)."""
    k = str(kind or "").strip().lower()
    if k not in ("asset", "debt"):
        return
    label = (name or "").strip() or "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = 0.0
    v = max(0.0, v)
    if v <= 0 and label == "—":
        return
    pid: Optional[str] = None
    if k == "asset" and portfolio_id:
        pid = str(portfolio_id).strip() or None
    hid = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO net_worth_value_history (id, user_id, recorded_at, kind, name, value, portfolio_id)
        VALUES (?, ?, datetime('now'), ?, ?, ?, ?)
        """,
        (hid, user_id, k, label, v, pid),
    )


def append_net_worth_value_history_snapshot(user_id: str, items: List[Dict[str, Any]]) -> int:
    """
    Append value snapshots (e.g. debounced client updates while editing).
    Each item: kind ('asset'|'debt'), name, value, optional portfolio_id (linked investment).
    """
    init_db()
    n = 0
    with get_db() as conn:
        for raw in items:
            if not isinstance(raw, dict):
                continue
            kind = str(raw.get("kind") or "").strip().lower()
            if kind not in ("asset", "debt"):
                continue
            name = str(raw.get("name") or raw.get("label") or "").strip() or "—"
            try:
                val = float(raw.get("value") if raw.get("value") is not None else raw.get("price") or 0)
            except (TypeError, ValueError):
                val = 0.0
            raw_pid = raw.get("portfolio_id")
            pid_s = str(raw_pid).strip() if raw_pid else None
            if kind == "debt":
                pid_s = None
            elif pid_s:
                prow = get_portfolio(pid_s)
                if not prow or prow.get("user_id") != user_id:
                    continue
            _insert_net_worth_value_history_row(conn, user_id, kind, name, val, pid_s)
            n += 1
    return n


def _nw_event_state_key(kind: str, name: str, portfolio_id: Optional[str]) -> str:
    k = (kind or "").strip().lower()
    label = (name or "").strip() or "—"
    if k == "asset" and portfolio_id and str(portfolio_id).strip():
        return f"asset|{str(portfolio_id).strip()}"
    if k == "asset":
        return f"asset|n|{label}"
    return f"debt|{label}"


def _nw_assets_by_display(asset_state: Dict[str, float], labels: Dict[str, str]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for sk, v in asset_state.items():
        if v <= 0:
            continue
        disp = labels.get(sk, sk)
        out[disp] = out.get(disp, 0.0) + float(v)
    return out


def _nw_debts_by_display(debt_state: Dict[str, float], labels: Dict[str, str]) -> Dict[str, float]:
    """Positive magnitudes per debt label for chart stacking below zero."""
    out: Dict[str, float] = {}
    for sk, v in debt_state.items():
        if v <= 0:
            continue
        disp = labels.get(sk, sk)
        out[disp] = out.get(disp, 0.0) + float(v)
    return out


def _nw_seed_state_from_entries(user_id: str) -> Tuple[Dict[str, float], Dict[str, float], Dict[str, str]]:
    buckets = list_net_worth_entries(user_id)
    asset_m: Dict[str, float] = {}
    debt_m: Dict[str, float] = {}
    labels: Dict[str, str] = {}
    for item in buckets.get("assets") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip() or "—"
        try:
            v = float(item.get("value") if item.get("value") is not None else item.get("price") or 0)
        except (TypeError, ValueError):
            v = 0.0
        v = max(0.0, v)
        pid = item.get("portfolio_id")
        if pid:
            sk = f"asset|{str(pid).strip()}"
        else:
            sk = f"asset|n|{label}"
        labels[sk] = label
        asset_m[sk] = v
    for item in buckets.get("debts") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip() or "—"
        try:
            v = float(item.get("value") if item.get("value") is not None else item.get("price") or 0)
        except (TypeError, ValueError):
            v = 0.0
        v = max(0.0, v)
        sk = f"debt|{label}"
        labels[sk] = label
        debt_m[sk] = v
    return asset_m, debt_m, labels


def _iter_days_iso(d0: str, d1: str) -> List[str]:
    a = datetime.strptime(d0[:10], "%Y-%m-%d").date()
    b = datetime.strptime(d1[:10], "%Y-%m-%d").date()
    if a > b:
        return []
    out: List[str] = []
    cur = a
    while cur <= b:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def _nw_apply_live_linked_portfolio_values(user_id: str, asset_map: Dict[str, float]) -> None:
    """In-place: set each ``asset|{{portfolio_id}}`` amount to current ``saved_portfolios.portfolio_value``."""
    for sk in list(asset_map.keys()):
        if not sk.startswith("asset|"):
            continue
        rest = sk[6:]
        if not rest or rest.startswith("n|"):
            continue
        pid = rest.strip()
        if not pid:
            continue
        prow = get_portfolio(pid)
        if not prow or prow.get("user_id") != user_id:
            asset_map.pop(sk, None)
            continue
        raw_pv = prow.get("portfolio_value")
        try:
            pv = float(raw_pv) if raw_pv is not None else None
        except (TypeError, ValueError):
            pv = None
        if pv is not None and pv >= 0:
            asset_map[sk] = pv


def _nw_min_entry_created_date(user_id: str) -> Optional[str]:
    """First calendar day any net worth row existed (ISO date), or None."""
    init_db()
    with get_db() as conn:
        r = conn.execute(
            "SELECT MIN(created_at) AS m FROM net_worth_entries WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not r or r["m"] is None:
        return None
    s = str(r["m"])
    return s[:10] if len(s) >= 10 else None


def build_net_worth_chart_series(user_id: str) -> Dict[str, Any]:
    """
    Daily points for net worth UI chart: ``value`` = net (assets − debts);
    ``by_ticker`` = asset amounts (stacked above zero); ``by_debt`` = debt magnitudes
    (stacked below zero in the UI). Replays ``net_worth_value_history`` with forward-filled state;
    if no history, repeats the current worksheet snapshot across the last year.
    """
    init_db()
    today = datetime.utcnow().strftime("%Y-%m-%d")
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT recorded_at, kind, name, value, portfolio_id
            FROM net_worth_value_history
            WHERE user_id = ?
            ORDER BY recorded_at ASC, id ASC
            """,
            (user_id,),
        ).fetchall()

    series: List[Dict[str, Any]] = []
    seed_a, seed_d, seed_lab = _nw_seed_state_from_entries(user_id)
    d_created = _nw_min_entry_created_date(user_id)

    if not rows:
        if not seed_a and not seed_d:
            return {"series": [], "valuation_as_of": None}
        d0 = d_created or (datetime.utcnow() - timedelta(days=365)).strftime("%Y-%m-%d")
        days = _iter_days_iso(d0, today)
        for d in days:
            a_eff = dict(seed_a)
            if d == today:
                _nw_apply_live_linked_portfolio_values(user_id, a_eff)
            by_disp = _nw_assets_by_display(a_eff, seed_lab)
            by_debt_disp = _nw_debts_by_display(seed_d, seed_lab)
            net = sum(a_eff.values()) - sum(seed_d.values())
            pt: Dict[str, Any] = {"date": d, "value": float(net)}
            if by_disp:
                pt["by_ticker"] = dict(by_disp)
            if by_debt_disp:
                pt["by_debt"] = dict(by_debt_disp)
            series.append(pt)
        return {"series": series, "valuation_as_of": today}

    by_day: Dict[str, List[Any]] = defaultdict(list)
    for r in rows:
        day = str(r["recorded_at"])[:10]
        by_day[day].append(r)

    sorted_event_days = sorted(by_day.keys())
    event_start = sorted_event_days[0]
    d0 = min(event_start, d_created) if d_created else event_start
    days = _iter_days_iso(d0, today)

    asset_state: Dict[str, float] = {}
    debt_state: Dict[str, float] = {}
    labels: Dict[str, str] = {}
    entered_replay = False

    for d in days:
        if d < event_start:
            a_m, d_m, lb = seed_a, seed_d, seed_lab
        else:
            if not entered_replay:
                asset_state.clear()
                debt_state.clear()
                labels.clear()
                entered_replay = True
            for r in by_day.get(d, []):
                kind = str(r["kind"] or "").strip().lower()
                name = str(r["name"] or "")
                try:
                    val = float(r["value"] or 0)
                except (TypeError, ValueError):
                    val = 0.0
                val = max(0.0, val)
                pid_raw = r["portfolio_id"] if "portfolio_id" in r.keys() else None
                pid = str(pid_raw).strip() if pid_raw else None
                sk = _nw_event_state_key(kind, name, pid)
                labels[sk] = (name or "").strip() or "—"
                if kind == "asset":
                    asset_state[sk] = val
                elif kind == "debt":
                    debt_state[sk] = val
            a_m, d_m, lb = asset_state, debt_state, labels

        a_eff = dict(a_m)
        if d == today:
            _nw_apply_live_linked_portfolio_values(user_id, a_eff)
        gross_a = sum(a_eff.values())
        gross_d = sum(d_m.values())
        net = gross_a - gross_d
        by_disp = _nw_assets_by_display(a_eff, lb)
        by_debt_disp = _nw_debts_by_display(d_m, lb)
        pt: Dict[str, Any] = {"date": d, "value": float(net)}
        if by_disp:
            pt["by_ticker"] = dict(by_disp)
        if by_debt_disp:
            pt["by_debt"] = dict(by_debt_disp)
        series.append(pt)

    return {"series": series, "valuation_as_of": today}


def replace_net_worth_entries(
    user_id: str,
    assets: List[Dict[str, Any]],
    debts: List[Dict[str, Any]],
    linked_portfolio_ids: Optional[List[str]] = None,
    linked_portfolio_yoy: Optional[Dict[str, Any]] = None,
    *,
    record_value_history: bool = True,
) -> None:
    """Replace all asset/debt rows for user; linked portfolios become asset rows with portfolio_id set."""
    init_db()
    linked_portfolio_ids = linked_portfolio_ids or []
    yoy_map: Dict[str, float] = {}
    if isinstance(linked_portfolio_yoy, dict):
        for k, v in linked_portfolio_yoy.items():
            ks = str(k).strip()
            if not ks:
                continue
            try:
                yoy_map[ks] = float(v)
            except (TypeError, ValueError):
                yoy_map[ks] = 0.0
    seen_linked: set[str] = set()
    linked_rows: List[Tuple[str, str, float, float, Optional[str]]] = []
    for pid in linked_portfolio_ids:
        pid_s = str(pid).strip()
        if not pid_s or pid_s in seen_linked:
            continue
        seen_linked.add(pid_s)
        prow = get_portfolio(pid_s)
        if not prow or prow.get("user_id") != user_id:
            continue
        label = str(prow.get("portfolio_name") or "Portfolio").strip() or "Portfolio"
        raw_pv = prow.get("portfolio_value")
        try:
            pval = float(raw_pv) if raw_pv is not None else 0.0
        except (TypeError, ValueError):
            pval = 0.0
        pyoy = float(yoy_map.get(pid_s, 0.0))
        pca = prow.get("created_at")
        nw_created = str(pca).strip() if pca else None
        linked_rows.append((pid_s, label, max(0.0, pval), pyoy, nw_created))

    def _norm_line(it: Dict[str, Any]) -> tuple[str, float, float]:
        label = str(it.get("label") or "").strip() or "—"
        raw_v = it.get("price") if it.get("price") is not None else it.get("value")
        try:
            value = float(raw_v or 0)
        except (TypeError, ValueError):
            value = 0.0
        try:
            yoy = float(it.get("yoy_pct") or 0)
        except (TypeError, ValueError):
            yoy = 0.0
        return label, max(0.0, value), yoy

    with get_db() as conn:
        conn.execute("DELETE FROM net_worth_entries WHERE user_id = ?", (user_id,))
        for it in assets:
            label, value, yoy = _norm_line(it if isinstance(it, dict) else {})
            if value <= 0 and label == "—":
                continue
            eid = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO net_worth_entries (
                    id, user_id, kind, label, value, yoy_pct, portfolio_id, created_at, updated_at
                )
                VALUES (?, ?, 'asset', ?, ?, ?, NULL, datetime('now'), datetime('now'))
                """,
                (eid, user_id, label, value, yoy),
            )
            if record_value_history:
                _insert_net_worth_value_history_row(conn, user_id, "asset", label, value, None)
        for pid_s, label, pval, pyoy, nw_created_at in linked_rows:
            eid = str(uuid.uuid4())
            if nw_created_at:
                conn.execute(
                    """
                    INSERT INTO net_worth_entries (
                        id, user_id, kind, label, value, yoy_pct, portfolio_id, created_at, updated_at
                    )
                    VALUES (?, ?, 'asset', ?, ?, ?, ?, ?, datetime('now'))
                    """,
                    (eid, user_id, label, pval, pyoy, pid_s, nw_created_at),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO net_worth_entries (
                        id, user_id, kind, label, value, yoy_pct, portfolio_id, created_at, updated_at
                    )
                    VALUES (?, ?, 'asset', ?, ?, ?, ?, datetime('now'), datetime('now'))
                    """,
                    (eid, user_id, label, pval, pyoy, pid_s),
                )
            if record_value_history:
                _insert_net_worth_value_history_row(conn, user_id, "asset", label, pval, pid_s)
        for itk in debts:
            label, value, yoy = _norm_line(itk if isinstance(itk, dict) else {})
            if value <= 0 and label == "—":
                continue
            eid = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO net_worth_entries (
                    id, user_id, kind, label, value, yoy_pct, portfolio_id, created_at, updated_at
                )
                VALUES (?, ?, 'debt', ?, ?, ?, NULL, datetime('now'), datetime('now'))
                """,
                (eid, user_id, label, value, yoy),
            )
            if record_value_history:
                _insert_net_worth_value_history_row(conn, user_id, "debt", label, value, None)


def get_user_net_worth(user_id: str) -> Optional[Dict[str, Any]]:
    """
    Return net worth document: assets, debts (with id, timestamps), linked_portfolio_ids.
    Migrates legacy single-blob JSON into net_worth_entries on first read.
    """
    init_db()
    with get_db() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM net_worth_entries WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    count = int(n["c"]) if n else 0
    if count == 0:
        _migrate_legacy_net_worth_blob(user_id)

    buckets = list_net_worth_entries(user_id)
    buckets = _prune_net_worth_buckets_for_valid_portfolios(user_id, buckets)
    linked = get_user_net_worth_linked_portfolio_ids(user_id)

    if not buckets["assets"] and not buckets["debts"] and not linked:
        return None

    return {
        "assets": buckets["assets"],
        "debts": buckets["debts"],
        "linked_portfolio_ids": linked,
    }


def upsert_user_net_worth(user_id: str, data: Dict[str, Any]) -> None:
    """Persist assets and debts as rows; linked portfolio ids in user_net_worth."""
    init_db()
    assets = data.get("assets") if isinstance(data.get("assets"), list) else []
    debts = data.get("debts") if isinstance(data.get("debts"), list) else []
    linked = data.get("linked_portfolio_ids")
    linked_clean = [str(x) for x in linked] if isinstance(linked, list) else []
    linked_yoy = data.get("linked_portfolio_yoy")
    replace_net_worth_entries(
        user_id,
        assets,
        debts,
        linked_portfolio_ids=linked_clean,
        linked_portfolio_yoy=linked_yoy if isinstance(linked_yoy, dict) else None,
    )
    with get_db() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM net_worth_entries WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    cnt = int(n["c"]) if n else 0
    if cnt == 0 and not linked_clean:
        with get_db() as conn:
            conn.execute("DELETE FROM user_net_worth WHERE user_id = ?", (user_id,))
        return
    upsert_user_net_worth_links(user_id, linked_clean)
