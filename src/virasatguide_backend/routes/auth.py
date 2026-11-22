"""Registration, login, token validation, logout, and profile routes."""

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from virasatguide_backend.data.database import get_users_collection
from virasatguide_backend.data.profiles import ProfileUpdate, PublicProfile, public_profile
from virasatguide_backend.data.security import (
    ALGORITHM,
    AUTH_COOKIE,
    get_frontend_origin,
    create_access_token,
    get_password_hash,
    get_secret_key,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=100, pattern=r"^\S+$")
    password: str = Field(min_length=8, max_length=1024)


@router.post("/register", status_code=201)
async def register_user(user: UserCreate, users=Depends(get_users_collection)):
    if user.username.startswith("google:"):
        raise HTTPException(status_code=400, detail="This username prefix is reserved")
    # Hash passwords without blocking other requests.
    hashed_password = await run_in_threadpool(get_password_hash, user.password)
    try:
        await users.insert_one({
            "username": user.username,
            "password": hashed_password,
            "token_version": 1,
        })
    except DuplicateKeyError:
        # MongoDB prevents duplicate usernames, including simultaneous signups.
        raise HTTPException(status_code=400, detail="Username already registered") from None
    return {"message": "User created successfully."}


@router.post("/login")
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    users=Depends(get_users_collection),
):
    user = await users.find_one({"username": form_data.username})
    if not user or not await run_in_threadpool(
        verify_password, form_data.password, user.get("password", "")
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
    credentials_exception = HTTPException(
        status_code=401, detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if token is None:
        token = request.cookies.get(AUTH_COOKIE)
        # Cookies are attached automatically: require our trusted frontend for writes.
        if token and request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            frontend_origin = get_frontend_origin()
            if not frontend_origin or origin != frontend_origin:
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

    user = await users.find_one({"username": username})
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
    await users.update_one(
        {"_id": current_user["_id"]},
        [{"$set": {"token_version": {"$add": [{"$ifNull": ["$token_version", 1]}, 1]}}}],
    )
    response.delete_cookie(AUTH_COOKIE, path="/")
    return {"message": "Successfully logged out of all devices."}


@router.get("/me", response_model=PublicProfile)
async def read_users_me(response: Response, current_user: dict = Depends(get_current_user)):
    response.headers["Cache-Control"] = "private, no-store"
    return public_profile(current_user)


@router.put("/profile", response_model=PublicProfile)
async def update_profile(
    profile: ProfileUpdate, response: Response,
    current_user: dict = Depends(get_current_user), users=Depends(get_users_collection),
):
    # Ownership comes from the verified login, never from submitted profile fields.
    fields = profile.model_dump(mode="json")
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
