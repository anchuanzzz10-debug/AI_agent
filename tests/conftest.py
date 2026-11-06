import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from guardian.models import ProjectConfig  # noqa: E402
from guardian.storage import ProjectStore  # noqa: E402
from guardian.vault import Vault  # noqa: E402

PASS = "correct horse battery staple"


@pytest.fixture
def home(tmp_path):
    return tmp_path / "data"


@pytest.fixture
def vault(home):
    home.mkdir(parents=True, exist_ok=True)
    return Vault.initialize(home, PASS)


def make_project(pid="site-one", ptype="wordpress", seo=False, **kw):
    return ProjectConfig(id=pid, name=pid.title(), type=ptype, url="https://site.test",
                         features={"seo": seo}, **kw)


@pytest.fixture
def store(home):
    return ProjectStore(home, "site-one")


def cert_in(days):
    def fetch(host, port):
        return datetime.now(timezone.utc) + timedelta(days=days, hours=1)
    return fetch
