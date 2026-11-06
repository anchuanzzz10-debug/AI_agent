import shutil

import pytest

from conftest import PASS, make_project
from guardian.models import ConfigError, ProjectConfig
from guardian.registry import Registry
from guardian.storage import ProjectStore
from guardian.vault import Vault, VaultError


def test_defaults_per_type_and_seo_off_by_default():
    wp = make_project()
    app = make_project("app-one", "application")
    assert wp.enabled("wordpress") and not wp.enabled("seo")
    assert app.enabled("health_endpoint") and not app.enabled("seo") and not app.enabled("wordpress")
    assert app.health_path == "/health"


def test_seo_chosen_at_connect_time_and_changeable():
    p = make_project(seo=True)
    assert p.enabled("seo")
    p.set_feature("seo", False)
    assert not p.enabled("seo")


def test_incompatible_features_refused():
    with pytest.raises(ConfigError):
        make_project("app-one", "application").set_feature("wordpress", True)
    with pytest.raises(ConfigError):
        ProjectConfig(id="app-two", name="x", type="application", url="https://a.test",
                      features={"wordpress": True})


@pytest.mark.parametrize("bad", ["../etc", "A", "x", "has space", "a/b"])
def test_invalid_ids_rejected(bad):
    with pytest.raises(ConfigError):
        make_project(bad)


def test_registry_scales_by_adding_files(tmp_path):
    reg = Registry(tmp_path)
    for i in range(1, 5):
        reg.add(make_project(f"site-{i}", seo=i % 2 == 0))
    reg.add(make_project("app-one", "application"))
    projects = reg.load_all()
    assert [p.id for p in projects] == ["app-one", "site-1", "site-2", "site-3", "site-4"]
    reg.set_feature("site-1", "seo", True)
    assert reg.get("site-1").enabled("seo")
    with pytest.raises(ConfigError):
        reg.add(make_project("site-1"))


def test_vault_wrong_passphrase(home, vault):
    with pytest.raises(VaultError):
        Vault.open(home, "wrong passphrase!!")
    assert Vault.open(home, PASS)


def test_credentials_isolated_between_projects(home, vault):
    a, b = vault.project("site-one"), vault.project("site-two")
    a.set("wp_read_app_password", "secret-A")
    b.set("wp_read_app_password", "secret-B")
    assert a.get("wp_read_app_password") == "secret-A"
    assert b.get("wp_read_app_password") == "secret-B"
    # Copy A's encrypted file over B's: B's key must not decrypt it.
    shutil.copy(home / "projects/site-one/credentials.enc", home / "projects/site-two/credentials.enc")
    with pytest.raises(VaultError):
        b.get("wp_read_app_password")


def test_credential_values_never_listed(vault):
    c = vault.project("site-one")
    c.set("wp_read_app_password", "s3cret")
    assert c.names() == ["wp_read_app_password"]
    assert "s3cret" not in repr(c)


def test_storage_is_per_project_and_traversal_safe(home):
    s1, s2 = ProjectStore(home, "site-one"), ProjectStore(home, "site-two")
    s1.audit("t", "event", x=1)
    assert len(s1.audit_entries()) == 1 and s2.audit_entries() == []
    assert s1.db_path != s2.db_path
    with pytest.raises(ConfigError):
        s1.path("..", "site-two", "guardian.db")
    with pytest.raises(ConfigError):
        ProjectStore(home, "../evil")
