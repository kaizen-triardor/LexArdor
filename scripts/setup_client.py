#!/usr/bin/env python3
"""Setup a LexArdor client installation.

Usage:
  python3 scripts/setup_client.py --firm "Advokatska kancelarija Petrović" --user petrovic --pass "SecurePass123"
  python3 scripts/setup_client.py --add-user --user marko --pass "Pass456" --role user
  python3 scripts/setup_client.py --list-users
  python3 scripts/setup_client.py --reset-password --user petrovic --pass "NewPass789"
"""
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent))

import argparse
from passlib.hash import bcrypt
from db.models import get_db, init_db, get_user, create_user


def list_users():
    conn = get_db()
    users = conn.execute("SELECT id, username, role, created_at FROM users").fetchall()
    conn.close()
    if not users:
        print("No users found. Run setup first.")
        return
    print(f"{'ID':<4} {'Username':<20} {'Role':<10} {'Created'}")
    print("-" * 60)
    for u in users:
        print(f"{u[0]:<4} {u[1]:<20} {u[2]:<10} {u[3]}")


def add_user(username, password, role="user"):
    init_db()
    if get_user(username):
        print(f"User '{username}' already exists.")
        return False
    pw_hash = bcrypt.hash(password)
    uid = create_user(username, pw_hash, role)
    print(f"Created user '{username}' (ID: {uid}, role: {role})")
    return True


def reset_password(username, new_password):
    conn = get_db()
    user = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if not user:
        print(f"User '{username}' not found.")
        conn.close()
        return False
    pw_hash = bcrypt.hash(new_password)
    conn.execute("UPDATE users SET password_hash = ? WHERE username = ?", (pw_hash, username))
    conn.commit()
    conn.close()
    print(f"Password reset for '{username}'")
    return True


def setup_firm(firm_name, admin_user, admin_pass):
    """Full client setup — wipe users table and create fresh admin."""
    init_db()
    conn = get_db()
    conn.execute("DELETE FROM users")
    conn.commit()
    conn.close()

    pw_hash = bcrypt.hash(admin_pass)
    uid = create_user(admin_user, pw_hash, "admin")
    print(f"Client installation configured:")
    print(f"  Firm: {firm_name}")
    print(f"  Admin: {admin_user} (ID: {uid})")
    print(f"  Password: {'*' * len(admin_pass)}")
    print(f"\nLexArdor is ready for {firm_name}.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="LexArdor client setup")
    p.add_argument("--firm", help="Law firm name (for fresh setup)")
    p.add_argument("--user", "-u", help="Username")
    p.add_argument("--pass", "-p", dest="password", help="Password")
    p.add_argument("--role", default="user", choices=["admin", "user"], help="User role")
    p.add_argument("--add-user", action="store_true", help="Add a new user")
    p.add_argument("--list-users", action="store_true", help="List all users")
    p.add_argument("--reset-password", action="store_true", help="Reset user password")
    args = p.parse_args()

    if args.list_users:
        list_users()
    elif args.firm and args.user and args.password:
        setup_firm(args.firm, args.user, args.password)
    elif args.add_user and args.user and args.password:
        add_user(args.user, args.password, args.role)
    elif args.reset_password and args.user and args.password:
        reset_password(args.user, args.password)
    else:
        p.print_help()
        print("\nExamples:")
        print('  Setup new client:  python3 scripts/setup_client.py --firm "Kancelarija Petrović" --user petrovic --pass "SecurePass123"')
        print('  Add user:          python3 scripts/setup_client.py --add-user --user marko --pass "Pass456"')
        print('  List users:        python3 scripts/setup_client.py --list-users')
        print('  Reset password:    python3 scripts/setup_client.py --reset-password --user petrovic --pass "NewPass789"')
