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
    coming: bool = True
    allergies: str | None = Field(None, max_length=500)
    transport_assist: bool = False


class RsvpOut(BaseModel):
    """RSVP as returned from API."""
    id: int
    name: str
    email: str
    coming: bool
    allergies: str | None
    transport_assist: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class RsvpSubmitResponse(BaseModel):
    """Response after submitting an RSVP (POST /rsvps)."""
    status: str = "ok"
    message: str = "RSVP submitted successfully."
    updated: bool = False  # True if an existing RSVP was replaced


# ----- Photos (guest uploads on the photo page) -----

MAX_FILES_PER_REQUEST = 30


class PhotoUploadRequestItem(BaseModel):
    """One file a guest wants to upload. Size is a hint; the bucket enforces it."""
    content_type: str = Field(..., max_length=100)
    size_bytes: int = Field(..., ge=0)


class PhotoUploadRequest(BaseModel):
    """Ask for presigned upload forms for a batch of files."""
    files: list[PhotoUploadRequestItem] = Field(..., min_length=1, max_length=MAX_FILES_PER_REQUEST)


class PhotoUploadTarget(BaseModel):
    """Where and how the browser should POST one file."""
    key: str
    url: str
    fields: dict[str, str]


class PhotoUploadResponse(BaseModel):
    """Presigned targets, in the same order as the requested files."""
    targets: list[PhotoUploadTarget]


class PhotoCreate(BaseModel):
    """Record a file that finished uploading."""
    storage_key: str = Field(..., min_length=1, max_length=500)
    # Small rendition for the gallery grid, uploaded by the browser alongside
    # the full file. Optional: a photo without one still records fine.
    thumb_key: str | None = Field(None, min_length=1, max_length=500)
    uploader_name: str | None = Field(None, max_length=255)
    caption: str | None = Field(None, max_length=500)
    width: int | None = Field(None, ge=0)
    height: int | None = Field(None, ge=0)
    duration_seconds: float | None = Field(None, ge=0)


class PhotoOut(BaseModel):
    """A stored photo/video, as returned to the admin gallery."""
    id: int
    storage_key: str
    thumb_key: str | None = None
    uploader_name: str | None
    caption: str | None
    content_type: str
    size_bytes: int
    width: int | None
    height: int | None
    duration_seconds: float | None
    created_at: datetime
    url: str | None = None  # presigned, added per request
    thumb_url: str | None = None  # presigned thumbnail, when one exists

    model_config = {"from_attributes": True}


class PhotoCountOut(BaseModel):
    """Public counter so the upload page can show progress without exposing files."""
    count: int


class PhotoCreateResponse(BaseModel):
    """Response after recording an upload."""
    status: str = "ok"
    message: str = "Bilden är uppladdad."
    id: int


class PhotoPublicOut(BaseModel):
    """
    A photo as shown in the public gallery.

    Deliberately minimal: no uploader name, caption, file size or timestamp.
    Dimensions are included so the grid can reserve space and avoid reflow.
    """
    id: int
    url: str
    # Small rendition for the grid. Falls back to url when no thumbnail exists,
    # so the grid can always render something.
    thumb_url: str | None
    content_type: str
    width: int | None
    height: int | None


class PhotoThumbnailAttach(BaseModel):
    """Point an existing photo at a thumbnail already uploaded to the bucket."""
    thumb_key: str = Field(..., min_length=1, max_length=500)
