"""
Input/API layer: Pydantic models for validating incoming data.

Use these for:
- Request bodies (e.g. POST /users)
- Query params (optional, or separate small models)
- Any external input before it touches the DB

Validation, types, and docs live here; DB schema is separate.
"""

from datetime import datetime
from pydantic import BaseModel, EmailStr, Field


# ----- Example: user-related inputs -----

class UserCreate(BaseModel):
    """Input for creating a user."""
    email: EmailStr
    name: str = Field(..., min_length=1, max_length=255)
    is_active: bool = True


class UserUpdate(BaseModel):
    """Input for updating a user (all fields optional)."""
    email: EmailStr | None = None
    name: str | None = Field(None, min_length=1, max_length=255)
    is_active: bool | None = None


# ----- Example: response / DB-shaped output (optional) -----

class UserOut(BaseModel):
    """User as returned from API (matches DB row shape)."""
    id: int
    email: str
    name: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}  # for ORM/DB row → model


# ----- RSVP (frontend form submission) -----

class RsvpCreate(BaseModel):
    """Input for submitting an RSVP from the frontend."""
    name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    attending: bool = True
    message: str | None = Field(None, max_length=1000)


class RsvpOut(BaseModel):
    """RSVP as returned from API."""
    id: int
    name: str
    email: str
    attending: bool
    message: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
