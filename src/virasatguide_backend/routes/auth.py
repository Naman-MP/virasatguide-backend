"""Registration, login, token validation, logout, and profile routes."""

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from virasatguide_backend.data.credentials import CredentialsInput, login_fields, login_name_query, public_login_fields
from virasatguide_backend.data.database import get_users_collection
from virasatguide_backend.data.profiles import ProfileUpdate, PublicProfile, public_profile
from virasatguide_backend.data.security import (
    ALGORITHM,
    AUTH_COOKIE,
    get_trusted_origins,
    create_access_token,
    get_password_hash,
    get_secret_key,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)


# Keep the registration schema name available to existing imports.
UserCreate = CredentialsInput


@router.post("/register", status_code=201)
async def register_user(user: UserCreate, users=Depends(get_users_collection)):
    """Create a local password account if its normalized login name is available."""
    try:
        if await users.find_one(login_name_query(user.username)):
            raise HTTPException(400, "Username already registered")
        hashed = await run_in_threadpool(get_password_hash, user.password.get_secret_value())
        await users.insert_one({"username": user.username, "password": hashed,
                                "token_version": 1, **login_fields(user.username)})
    except DuplicateKeyError:
        raise HTTPException(400, "Username already registered") from None
    except PyMongoError as exc:
        raise HTTPException(503, "Account storage is unavailable") from exc
    return {"message": "User created successfully."}


@router.post("/login")
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    users=Depends(get_users_collection),
):
    """Verify submitted credentials and issue a token for the stable account identity."""
    name_query = login_name_query(form_data.username)
    try:
        matches = await users.find(name_query).limit(2).to_list()
        user = matches[0] if len(matches) == 1 else None
        if len(matches) == 2:
            # Ambiguous legacy logins must match the original casing, even if there are more than two.
            user = await users.find_one({**name_query, "username": form_data.username})
    except PyMongoError as exc:
        raise HTTPException(503, "Account storage is unavailable") from exc
    if not user or not await run_in_threadpool(
        verify_password, form_data.password, user.get("password") or ""
    ):
        raise HTTPException(
            status_code=401, detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token({
        "sub": user["username"], "version": user.get("token_version", 1),
    })
    return {"access_token": access_token, "token_type": "bearer"}


async def get_current_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    users=Depends(get_users_collection),
):
    """Authenticate a bearer or cookie token and enforce origin checks on cookie writes."""
    credentials_exception = HTTPException(
        status_code=401, detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if token is None:
        token = request.cookies.get(AUTH_COOKIE)
        # Cookies are attached automatically: require our trusted frontend for writes.
        if token and request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin not in get_trusted_origins():
                raise HTTPException(403, "Untrusted request origin")
    if not token:
        raise credentials_exception
    try:
        # Tokens must be valid, unexpired, and contain these fields.
        payload = jwt.decode(
            token, get_secret_key(), algorithms=[ALGORITHM],
            options={"require": ["exp", "sub", "version"]},
        )
        username = payload["sub"]
        token_version = payload["version"]
        if not isinstance(username, str) or not username or type(token_version) is not int:
            raise credentials_exception
    except jwt.InvalidTokenError:
        raise credentials_exception from None

    try:
        user = await users.find_one({"username": username})
    except PyMongoError as exc:
        raise HTTPException(503, "Account storage is unavailable") from exc
    # A changed version means the user logged out. Older users start at version 1.
    if user is None or user.get("token_version", 1) != token_version:
        raise credentials_exception
    return user


@router.post("/logout")
async def logout(
    response: Response,
    current_user: dict = Depends(get_current_user),
    users=Depends(get_users_collection),
):
    # Increment the version atomically; a missing version starts at 1.
    """Revoke all existing tokens for the current account and clear the browser cookie."""
    await users.update_one(
        {"_id": current_user["_id"]},
        [{"$set": {"token_version": {"$add": [{"$ifNull": ["$token_version", 1]}, 1]}}}],
    )
    response.delete_cookie(AUTH_COOKIE, path="/")
    return {"message": "Successfully logged out of all devices."}


@router.get("/me", response_model=PublicProfile)
async def read_users_me(response: Response, current_user: dict = Depends(get_current_user)):
    """Return the current account's public profile with private caching rules."""
    response.headers["Cache-Control"] = "private, no-store"
    return public_profile(current_user)


@router.put("/profile", response_model=PublicProfile)
async def update_profile(
    profile: ProfileUpdate, response: Response,
    current_user: dict = Depends(get_current_user), users=Depends(get_users_collection),
):
    # Ownership comes from the verified login, never from submitted profile fields.
    """Save the signed-in user's personal details independently of login credentials."""
    fields = profile.model_dump(mode="json", exclude_unset=True)
    fields.setdefault("gender", "prefer_not_to_say")
    fields["onboarding_completed"] = True
    try:
        updated = await users.find_one_and_update(
            {"_id": current_user["_id"]}, {"$set": fields}, return_document=ReturnDocument.AFTER,
        )
    except PyMongoError as exc:
        raise HTTPException(503, "Profile storage is unavailable") from exc
    if updated is None:
        raise HTTPException(404, "Account not found")
    response.headers["Cache-Control"] = "private, no-store"
    return public_profile(updated)


@router.put("/credentials", response_model=PublicProfile, responses={
    401: {"description": "Sign in required."},
    403: {"description": "Only Google-linked accounts can set first-time credentials."},
    409: {"description": "Credentials already exist or the login name is taken."},
})
async def set_credentials(
    credentials: CredentialsInput, response: Response,
    current_user: dict = Depends(get_current_user), users=Depends(get_users_collection),
):
    """Atomically add first-time local credentials to the signed-in Google account."""
    if not current_user.get("google_sub"):
        raise HTTPException(403, "Credential setup requires a Google-linked account")
    if current_user.get("password") or public_login_fields(current_user)["credentials_set"]:
        raise HTTPException(409, "Sign-in credentials are already configured")
    try:
        if await users.find_one(login_name_query(credentials.username)):
            raise HTTPException(409, "Username already registered")
        hashed = await run_in_threadpool(get_password_hash, credentials.password.get_secret_value())
        # Atomic first-time setup: two requests cannot replace each other's password.
        updated = await users.find_one_and_update(
            {"_id": current_user["_id"], "google_sub": current_user["google_sub"],
             "password": {"$in": [None, ""]}},
            {"$set": {**login_fields(credentials.username), "password": hashed}},
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        raise HTTPException(409, "Username already registered") from None
    except PyMongoError as exc:
        raise HTTPException(503, "Account storage is unavailable") from exc
    if updated is None:
        raise HTTPException(409, "Sign-in credentials are already configured; refresh your profile")
    response.headers["Cache-Control"] = "private, no-store"
    return public_profile(updated)
