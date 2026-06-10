import enum
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import relationship

from crm.database import Base


class LeadStatus(str, enum.Enum):
    new = "new"
    verified = "verified"
    opted_out = "opted_out"
    drafted = "drafted"
    sent = "sent"
    replied = "replied"
    bounced = "bounced"


class JobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    finished = "finished"
    failed = "failed"


class ScrapeJob(Base):
    __tablename__ = "scrape_jobs"

    id = Column(Integer, primary_key=True)
    status = Column(Enum(JobStatus), default=JobStatus.pending, nullable=False)
    source = Column(String, default="meinestadt")
    cities_json = Column(JSON)
    total_found = Column(Integer, default=0)
    total_imported = Column(Integer, default=0)
    total_skipped = Column(Integer, default=0)
    total_failed = Column(Integer, default=0)
    current_city = Column(String)
    log_text = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True)
    company_name = Column(String, nullable=False)
    city = Column(String)
    category = Column(String)
    website = Column(String)
    email = Column(String)
    phone = Column(String)
    source_url = Column(String)
    status = Column(Enum(LeadStatus), default=LeadStatus.new, nullable=False)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    events = relationship("EmailEvent", back_populates="lead")
    tracking_links = relationship("TrackingLink", back_populates="lead")


class Template(Base):
    __tablename__ = "templates"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    subject = Column(String, nullable=False)
    body = Column(Text, nullable=False)

    campaigns = relationship("Campaign", back_populates="template")


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    template_id = Column(Integer, ForeignKey("templates.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    template = relationship("Template", back_populates="campaigns")
    events = relationship("EmailEvent", back_populates="campaign")


class EmailEvent(Base):
    __tablename__ = "email_events"

    id = Column(Integer, primary_key=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=True)
    event_type = Column(String, nullable=False)
    # 'metadata' is a reserved SQLAlchemy attribute; mapped to the 'metadata' DB column
    event_data = Column("metadata", JSON)
    created_at = Column(DateTime, default=datetime.utcnow)

    lead = relationship("Lead", back_populates="events")
    campaign = relationship("Campaign", back_populates="events", foreign_keys=[campaign_id])


class TrackingLink(Base):
    __tablename__ = "tracking_links"

    id = Column(Integer, primary_key=True)
    token = Column(String, unique=True, nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=True)
    destination_url = Column(String, nullable=False)
    clicked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    lead = relationship("Lead", back_populates="tracking_links")
    campaign = relationship("Campaign", foreign_keys=[campaign_id])
