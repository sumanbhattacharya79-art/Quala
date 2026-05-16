#!/usr/bin/env python3
"""Delete user and their saved portfolios from the database.
Usage:
  python scripts/delete_user_data.py              # delete all users and portfolios
  python scripts/delete_user_data.py --user-id X # delete specific user
  python scripts/delete_user_data.py --email X   # delete user by email
"""
import argparse
import sys
from pathlib import Path

# Add project root so we can import app.backend.db
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.backend.db import (
    delete_user,
    get_user_by_email,
    init_db,
)
from app.backend.db import get_db


def list_all_users():
    """List all users in the database."""
    init_db()
    with get_db() as conn:
        rows = conn.execute("SELECT user_id, email_id FROM user").fetchall()
    return [{"user_id": r["user_id"], "email_id": r["email_id"]} for r in rows]


def main():
    parser = argparse.ArgumentParser(description="Delete user and portfolios from DB")
    parser.add_argument("--user-id", help="User ID to delete")
    parser.add_argument("--email", help="Email of user to delete")
    parser.add_argument("--all", action="store_true", help="Delete ALL users and portfolios")
    parser.add_argument("--list", action="store_true", help="List users and exit")
    args = parser.parse_args()

    if args.list:
        users = list_all_users()
        if not users:
            print("No users in database.")
            return
        print("Users:")
        for u in users:
            print(f"  {u['email_id']} (user_id: {u['user_id']})")
        return

    if args.all:
        users = list_all_users()
        if not users:
            print("No users to delete.")
            return
        for u in users:
            print(f"Deleting user {u['email_id']} ({u['user_id']})...")
            deleted = delete_user(u["user_id"])
            print(f"  Deleted: {deleted}")
        print("Done. Clear localStorage in browser: open DevTools > Application > Local Storage > delete portfolio-optimizer-user-id, portfolio-optimizer-user-email, portfolio-optimizer-form-state, portfolio-optimizer-session")
        return

    user_id = args.user_id
    if args.email:
        user = get_user_by_email(args.email)
        if not user:
            print(f"User not found: {args.email}")
            sys.exit(1)
        user_id = user["user_id"]
        print(f"Found user: {user['email_id']} ({user_id})")

    if not user_id:
        print("Specify --user-id, --email, or --all")
        parser.print_help()
        sys.exit(1)

    deleted = delete_user(user_id)
    if deleted:
        print(f"Deleted user {user_id} and their portfolios.")
    else:
        print(f"User not found: {user_id}")
        sys.exit(1)

    print("Clear localStorage in browser: DevTools > Application > Local Storage > delete:")
    print("  - portfolio-optimizer-user-id")
    print("  - portfolio-optimizer-user-email")
    print("  - portfolio-optimizer-form-state")
    print("  - portfolio-optimizer-session")


if __name__ == "__main__":
    main()
