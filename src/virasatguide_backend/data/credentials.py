"""Login names are separate from the stable account key used by JWTs and saved data."""

import re

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class CredentialsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")
    password: SecretStr = Field(min_length=8, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def reject_reserved_name(cls, value):
        if isinstance(value, str) and value.casefold().startswith("google:"):
            raise ValueError("The google: username prefix is reserved")
        return value


def login_fields(username: str) -> dict:
    return {"login_username": username, "login_username_normalized": username.casefold(),
            "credentials_set": True}


def public_login_fields(user: dict) -> dict:
    name = user.get("login_username")
    if not name and not user.get("google_sub") and not user["username"].startswith("google:"):
        name = user["username"] if user.get("password") else None
    return {"login_username": name, "credentials_set": bool(name and user.get("password"))}


def login_name_query(username: str) -> dict:
    # The fallback reserves old local usernames before their fields are backfilled.
    return {"$or": [
        {"login_username_normalized": username.casefold()},
        {"login_username_normalized": {"$in": [None, ""]},
         "username": {"$regex": "^" + re.escape(username) + "$", "$options": "i"},
         "password": {"$type": "string"}},
    ]}


async def migrate_login_names(users, *, apply: bool = False) -> dict:
    """Audit before writes; preserve account keys, hashes and all existing indexes."""
    documents = await users.find({}, {"username": 1, "google_sub": 1, "password": 1,
        "login_username": 1, "login_username_normalized": 1, "credentials_set": 1}).to_list()
    claims, updates = {}, []
    for user in documents:
        fields = public_login_fields(user)
        name = fields["login_username"]
        if name:
            claims.setdefault(name.casefold(), []).append(user)
    collisions = {name for name, owners in claims.items() if len(owners) > 1}
    for name, owners in claims.items():
        if name in collisions:
            continue  # Keep these legacy accounts and their exact-case logins usable.
        user = owners[0]
        values = login_fields(public_login_fields(user)["login_username"])
        if any(user.get(key) != value for key, value in values.items()):
            updates.append((user["_id"], user["username"], values))
    if apply:
        # Partial uniqueness allows Google accounts and unresolved legacy names.
        await users.create_index("login_username_normalized", unique=True,
            name="unique_login_username_normalized",
            partialFilterExpression={"login_username_normalized": {"$type": "string"}})
        for account_id, account_key, fields in updates:
            await users.update_one({"_id": account_id, "username": account_key}, {"$set": fields})
    return {"accounts_checked": len(documents), "accounts_to_backfill": len(updates), "collision_groups": len(collisions), "applied": apply}
