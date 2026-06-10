import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from crm.database import Base
from crm.models import Lead, LeadStatus


@pytest.fixture
def session():
    # StaticPool: all connections share a single underlying connection so the
    # in-memory DB (and its tables) is visible from any thread — required for
    # FastAPI TestClient which dispatches requests on a worker thread.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    sess = Session()
    yield sess
    sess.close()
    engine.dispose()


@pytest.fixture
def existing_lead(session):
    """A lead already in the DB for dedup-against-DB tests."""
    lead = Lead(
        company_name="Existing Corp",
        city="Hamburg",
        email="contact@existing.com",
        website="https://existing.com",
        status=LeadStatus.new,
    )
    session.add(lead)
    session.commit()
    return lead
