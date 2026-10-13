import os
import sqlite3
import time
from pathlib import Path

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

SECRET = os.getenv("DEMO_JWT_SECRET", "local-lab-only-change-before-sharing-123456789")
USERS = {
    "alice": {
        "tenant": "acme",
        "scopes": ["orders:read", "orders:write", "documents:read"],
    },
    "viewer": {"tenant": "acme", "scopes": ["orders:read", "documents:read"]},
    "bob": {
        "tenant": "beta",
        "scopes": ["orders:read", "orders:write", "documents:read"],
    },
}
bearer = HTTPBearer()


def issue_token(user):
    return jwt.encode(
        {
            "sub": user,
            **USERS[user],
            "aud": "harness-lab",
            "iss": "local-demo",
            "exp": int(time.time()) + 3600,
        },
        SECRET,
        algorithm="HS256",
    )


def decode_token(token):
    try:
        return jwt.decode(
            token,
            SECRET,
            algorithms=["HS256"],
            audience="harness-lab",
            issuer="local-demo",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid or expired demo session")


def principal(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    return decode_token(credentials.credentials)


def require(user, scope):
    if scope not in user["scopes"]:
        raise HTTPException(403, "Missing scope: " + scope)


def database(name):
    root = Path(os.getenv("DATA_DIR", ".data"))
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(root / (name + ".sqlite")), timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db
