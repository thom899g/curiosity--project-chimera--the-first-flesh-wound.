"""
Firebase Schema Definition for Chimera Protocol
Architectural Rationale: Using Pydantic for runtime validation ensures data integrity
before Firebase writes, preventing schema drift and enabling clear type hints.
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, validator
from uuid import uuid4


class UserProfile(BaseModel):
    """Core user entity with economic and behavioral tracking"""
    discord_id: str = Field(..., description="Unique Discord snowflake ID")
    server_ids: List[str] = Field(default_factory=list, description="Servers where user is licensed")
    license_key: Optional[str] = Field(None, description="Active license key")
    stripe_customer_id: Optional[str] = Field(None, description="Stripe customer reference")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_seen: datetime = Field(default_factory=datetime.utcnow)
    request_count: int = Field(default=0, ge=0, description="Total successful requests")
    total_tokens_used: int = Field(default=0, ge=0, description="Cumulative token consumption")
    is_suspended: bool = Field(default=False, description="Administrative suspension flag")
    
    @validator('discord_id')
    def validate_discord_id(cls, v):
        """Ensure Discord IDs are numeric strings"""
        if not v.isdigit():
            raise ValueError('Discord ID must be numeric string')
        return v


class LicenseRecord(BaseModel):
    """Economic unit representing a purchased license"""
    key: str = Field(..., description="Unique license key format: CHIMERA-XXXX-XXXX")
    user_id: str = Field(..., description="Discord ID of license owner")
    stripe_payment_id: str = Field(..., description="Stripe payment intent ID")
    purchase_date: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = Field(default=True)
    chargeback_status: str = Field(
        default="none", 
        regex="^(none|disputed|won|lost)$",
        description="Track payment disputes for economic resilience"
    )
    
    @validator('key')
    def validate_license_format(cls, v):
        if not v.startswith('CHIMERA-'):
            raise ValueError('License key must start with CHIMERA-')
        if len(v) != 21:  # CHIMERA-XXXXXXXX
            raise ValueError('Invalid license key length')
        return v


class RequestLog(BaseModel):
    """Immutable audit log for every AI inference"""
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    server_id: str
    channel_id: str
    input_text: str = Field(..., max_length=4000)
    output_text: str = Field(..., max_length=4000)
    model_used: str = Field(default="gpt-3.5-turbo")
    tokens_consumed: int = Field(..., ge=0)
    latency_ms: float = Field(..., ge=0.0)
    error_message: Optional[str] = Field(None)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    
    @validator('input_text', 'output_text')
    def sanitize_text(cls, v):
        """Basic sanitization to prevent injection attacks"""
        if v:
            # Remove null bytes and excessive whitespace
            v = v.replace('\x00', '').strip()
            if len(v) > 4000:
                v = v[:3997] + '...'
        return v


class RateLimitWindow(BaseModel):
    """Real-time rate limiting state for abuse prevention"""
    user_id: str
    window_start: datetime = Field(default_factory=datetime.utcnow)
    request_count: int = Field(default=0, ge=0)
    is_blocked: bool = Field(default=False)
    block_until: Optional[datetime] = Field(None)
    
    @validator('block_until')
    def validate_block_future(cls, v, values):
        if v and v <= datetime.utcnow():
            raise ValueError('Block time must be in future')
        return v