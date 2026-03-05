"""Shared fixtures for CRF-Migrate tests."""
import pytest
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PROFILES_DIR = Path(__file__).parent.parent / "profiles"


@pytest.fixture(scope="session")
def sample_acrf_path():
    """Path to the synthetic aCRF PDF (created on first use)."""
    pdf_path = FIXTURES_DIR / "sample_acrf.pdf"
    if not pdf_path.exists():
        from tests.fixtures.create_fixtures import create_sample_acrf
        create_sample_acrf()
    return pdf_path


@pytest.fixture(scope="session")
def cdisc_profile():
    """Load the cdisc_standard profile."""
    from src.profile_loader import load_profile
    return load_profile(PROFILES_DIR / "cdisc_standard.yaml")


@pytest.fixture(scope="session")
def cdisc_engine(cdisc_profile):
    """RuleEngine initialized with cdisc_standard profile."""
    from src.rule_engine import RuleEngine
    return RuleEngine(cdisc_profile)
