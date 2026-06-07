"""Tests for the device-model registry with auto-discovery."""

from __future__ import annotations

import sys
import textwrap
import types

import pytest

from custom_components.aqara_lanlink.device import registry
from custom_components.aqara_lanlink.device.base import Device


_SYNTH_PREFIX = "custom_components.aqara_lanlink.device.models.synth_"


@pytest.fixture(autouse=True)
def _reset_registry():
    # Pre-evict any synthetic subpackage modules from a prior test so each
    # test starts with a clean slate. Real production modules (camera_agl013 etc.) are
    # left in sys.modules so they can be returned from importlib cache without
    # re-executing their @register_device decorator.
    for key in list(sys.modules.keys()):
        if key.startswith(_SYNTH_PREFIX):
            del sys.modules[key]
    registry.reset_for_tests()
    yield
    # Post-test cleanup: evict synthetic modules so they do not leak into
    # subsequent tests via pkgutil.iter_modules or importlib cache.
    for key in list(sys.modules.keys()):
        if key.startswith(_SYNTH_PREFIX):
            del sys.modules[key]
    registry.reset_for_tests()


# ---------------------------------------------------------------------------
# Decorator behavior.
# ---------------------------------------------------------------------------


def test_register_device_single_model():
    @registry.register_device("test.model.alpha")
    class _Sub(Device):
        pass

    assert registry.get_device_class("test.model.alpha") is _Sub


def test_register_device_multiple_models_resolve_to_same_class():
    @registry.register_device("test.model.alpha", "test.model.beta")
    class _Sub(Device):
        pass

    assert registry.get_device_class("test.model.alpha") is _Sub
    assert registry.get_device_class("test.model.beta") is _Sub


def test_register_device_idempotent_same_class():
    class _Sub(Device):
        pass

    registry.register_device("test.model.alpha")(_Sub)
    # Re-registering the same model -> class is a no-op.
    registry.register_device("test.model.alpha")(_Sub)
    assert registry.get_device_class("test.model.alpha") is _Sub


def test_register_device_conflict_raises():
    class _A(Device):
        pass

    class _B(Device):
        pass

    registry.register_device("test.model.alpha")(_A)
    with pytest.raises(RuntimeError):
        registry.register_device("test.model.alpha")(_B)


def test_register_device_requires_model_string():
    with pytest.raises(ValueError):
        registry.register_device()


def test_register_device_rejects_non_device_subclass():
    class _NotADevice:
        pass

    with pytest.raises(TypeError):
        registry.register_device("test.model.alpha")(_NotADevice)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Lookup.
# ---------------------------------------------------------------------------


def test_get_device_class_returns_none_for_unknown_model():
    assert registry.get_device_class("test.model.nonexistent") is None


def test_known_models_returns_sorted_list():
    @registry.register_device("test.model.bravo", "test.model.alpha")
    class _Sub(Device):
        pass

    assert registry.known_models() == ["test.model.alpha", "test.model.bravo"]


# ---------------------------------------------------------------------------
# Auto-discovery via importing fake model packages.
# ---------------------------------------------------------------------------


def test_auto_discovery_imports_fake_model_module(monkeypatch, tmp_path):
    # Create a temporary fake package under sys.modules that masquerades
    # as `custom_components.aqara_lanlink.device.models` and contains a
    # single submodule that registers a synthetic device class.
    pkg_name = "custom_components.aqara_lanlink.device.models"
    submod_name = f"{pkg_name}.test_fake_model"

    fake_pkg = types.ModuleType(pkg_name)
    fake_pkg.__path__ = [str(tmp_path)]  # type: ignore[attr-defined]

    # Write a real .py file so pkgutil.iter_modules can discover it.
    submodule_path = tmp_path / "test_fake_model.py"
    submodule_path.write_text(textwrap.dedent(
        """
        from custom_components.aqara_lanlink.device import registry
        from custom_components.aqara_lanlink.device.base import Device

        @registry.register_device("test.model.fake")
        class FakeDevice(Device):
            MODEL = "test.model.fake"
        """
    ).strip())

    monkeypatch.setitem(sys.modules, pkg_name, fake_pkg)

    # Trigger discovery via lookup.
    cls = registry.get_device_class("test.model.fake")
    assert cls is not None
    assert cls.MODEL == "test.model.fake"


def test_auto_discovery_runs_only_once(monkeypatch, tmp_path):
    pkg_name = "custom_components.aqara_lanlink.device.models"
    fake_pkg = types.ModuleType(pkg_name)
    fake_pkg.__path__ = [str(tmp_path)]  # type: ignore[attr-defined]

    submodule_path = tmp_path / "test_counted_model.py"
    submodule_path.write_text(textwrap.dedent(
        """
        import os
        # Use a module-level counter that survives re-import attempts so
        # we can detect repeated discovery.
        counter = int(os.environ.get('AQARA_TEST_COUNTER', '0')) + 1
        os.environ['AQARA_TEST_COUNTER'] = str(counter)

        from custom_components.aqara_lanlink.device import registry
        from custom_components.aqara_lanlink.device.base import Device

        @registry.register_device("test.model.counted")
        class CountedDevice(Device):
            MODEL = "test.model.counted"
        """
    ).strip())

    monkeypatch.setitem(sys.modules, pkg_name, fake_pkg)
    monkeypatch.setenv("AQARA_TEST_COUNTER", "0")

    # First lookup: discovery runs.
    assert registry.get_device_class("test.model.counted") is not None
    first_count = int(__import__("os").environ.get("AQARA_TEST_COUNTER", "0"))
    # Second lookup: discovery should not re-run.
    assert registry.get_device_class("test.model.counted") is not None
    second_count = int(__import__("os").environ.get("AQARA_TEST_COUNTER", "0"))
    assert second_count == first_count


def test_auto_discovery_tolerates_broken_module(monkeypatch, tmp_path, caplog):
    pkg_name = "custom_components.aqara_lanlink.device.models"
    fake_pkg = types.ModuleType(pkg_name)
    fake_pkg.__path__ = [str(tmp_path)]  # type: ignore[attr-defined]

    # One broken module + one healthy module.
    (tmp_path / "test_broken_model.py").write_text(
        "raise RuntimeError('synthetic import failure')",
    )
    (tmp_path / "test_healthy_model.py").write_text(textwrap.dedent(
        """
        from custom_components.aqara_lanlink.device import registry
        from custom_components.aqara_lanlink.device.base import Device

        @registry.register_device("test.model.healthy")
        class HealthyDevice(Device):
            MODEL = "test.model.healthy"
        """
    ).strip())

    monkeypatch.setitem(sys.modules, pkg_name, fake_pkg)

    # Discovery should not raise; the healthy module should still register.
    cls = registry.get_device_class("test.model.healthy")
    assert cls is not None


def test_auto_discovery_handles_missing_models_package(monkeypatch):
    # Stub out the models package so importlib raises ImportError.
    pkg_name = "custom_components.aqara_lanlink.device.models"
    # Force ImportError by patching importlib.import_module within the
    # registry module to raise for the models package only.
    import importlib

    real_import = importlib.import_module

    def _raising_import(name, *args, **kwargs):
        if name == pkg_name:
            raise ImportError("synthetic missing models package")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(
        "custom_components.aqara_lanlink.device.registry.importlib.import_module",
        _raising_import,
    )
    # No raise; just an empty registry.
    assert registry.get_device_class("anything") is None
    assert registry.known_models() == []


# ---------------------------------------------------------------------------
# Synthetic package helper for catalog-index tests.
# ---------------------------------------------------------------------------


def _synth_package(
    monkeypatch, tmp_path, subpkg_name: str, init_src: str,
) -> None:
    """Install a synthetic model subpackage into the fake models package.

    Creates a directory under tmp_path named `subpkg_name`, writes
    `__init__.py` with `init_src`, and ensures the models package `__path__`
    includes tmp_path so that pkgutil.iter_modules discovers it.

    Synthetic subpackage names must start with "synth_" so the autouse
    `_reset_registry` fixture can evict them from sys.modules between tests.

    When called multiple times within a single test (to create multiple
    subpackages), the helper reuses the existing fake models package already
    installed in sys.modules rather than replacing it. The first call installs
    a fresh fake package via monkeypatch.setitem; subsequent calls on the same
    fake package simply extend its __path__ if needed.  This avoids silently
    discarding subpackages registered by earlier calls.
    """
    assert subpkg_name.startswith("synth_"), (
        f"_synth_package: subpkg_name must start with 'synth_', got {subpkg_name!r}"
    )
    pkg_name = "custom_components.aqara_lanlink.device.models"
    path_str = str(tmp_path)

    existing = sys.modules.get(pkg_name)
    # Reuse only if the installed module is already the synthetic fake owned by
    # this test (identified by the sentinel attribute). Any other module
    # (real device.models, or a stale entry from a previous test) is replaced.
    if existing is not None and getattr(existing, "_synth_test_pkg", False):
        # Reuse the already-installed fake package from an earlier _synth_package
        # call within this same test. Make sure tmp_path is in its __path__.
        if path_str not in existing.__path__:  # type: ignore[operator]
            existing.__path__.append(path_str)  # type: ignore[attr-defined]
    else:
        # First call in this test (or replacing a stale/real entry): install a
        # fresh fake models package via monkeypatch.setitem so that teardown
        # restores sys.modules[pkg_name] to its pre-test state. This prevents
        # the real device.models package's __path__ from being mutated.
        fake_pkg = types.ModuleType(pkg_name)
        fake_pkg.__path__ = [path_str]  # type: ignore[attr-defined]
        fake_pkg._synth_test_pkg = True  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, pkg_name, fake_pkg)

    # Create the subpackage directory and write __init__.py.
    subpkg_dir = tmp_path / subpkg_name
    subpkg_dir.mkdir(parents=True, exist_ok=True)
    (subpkg_dir / "__init__.py").write_text(textwrap.dedent(init_src).strip())


# ---------------------------------------------------------------------------
# Validation error tests (each must produce RuntimeError).
# ---------------------------------------------------------------------------


def test_validation_traits_without_models(monkeypatch, tmp_path):
    """TRAITS declared without MODELS raises RuntimeError."""
    _synth_package(monkeypatch, tmp_path, "synth_no_models", """
        from custom_components.aqara_lanlink.device.traits import TraitSpec
        TRAITS = {
            "1.7.85": TraitSpec(id="1.7.85", name="motion"),
        }
    """)
    with pytest.raises(RuntimeError, match="TRAITS.*declared without MODELS"):
        registry.get_device_class("anything")


def test_validation_whitelabels_without_models(monkeypatch, tmp_path):
    """WHITELABELS declared without MODELS raises RuntimeError."""
    _synth_package(monkeypatch, tmp_path, "synth_wl_no_models", """
        WHITELABELS = {"lumi.test.wl": "Test Variant"}
    """)
    with pytest.raises(
        RuntimeError, match="WHITELABELS.*declared without MODELS",
    ):
        registry.get_device_class("anything")


def test_validation_empty_models(monkeypatch, tmp_path):
    """Empty MODELS tuple raises RuntimeError."""
    _synth_package(monkeypatch, tmp_path, "synth_empty_models", """
        MODELS = ()
    """)
    with pytest.raises(RuntimeError, match="empty MODELS"):
        registry.get_device_class("anything")


def test_validation_duplicate_models_within_package(monkeypatch, tmp_path):
    """Duplicate entries within a single MODELS tuple raises RuntimeError."""
    _synth_package(monkeypatch, tmp_path, "synth_dup_models", """
        MODELS = ("lumi.test.dup", "lumi.test.dup")
        MANUFACTURER = "Aqara"
        DISPLAY_NAME = "Dup Test"
    """)
    with pytest.raises(RuntimeError, match="duplicate model.*MODELS"):
        registry.get_device_class("anything")


def test_validation_duplicate_model_across_packages(monkeypatch, tmp_path):
    """Two packages claiming the same model string raises RuntimeError."""
    _synth_package(monkeypatch, tmp_path, "synth_first", """
        MODELS = ("lumi.test.shared",)
        MANUFACTURER = "Aqara"
        DISPLAY_NAME = "First Package"
    """)
    _synth_package(monkeypatch, tmp_path, "synth_second", """
        MODELS = ("lumi.test.shared",)
        MANUFACTURER = "Aqara"
        DISPLAY_NAME = "Second Package"
    """)
    with pytest.raises(RuntimeError, match="model.*already claimed"):
        registry.get_device_class("anything")


def test_validation_override_class_for_unregistered_model(monkeypatch, tmp_path):
    """Override class registered for a model not in MODELS raises RuntimeError."""
    _synth_package(monkeypatch, tmp_path, "synth_bad_override", """
        from custom_components.aqara_lanlink.device.registry import register_device
        from custom_components.aqara_lanlink.device.base import Device

        MODELS = ("lumi.test.declared",)
        MANUFACTURER = "Aqara"
        DISPLAY_NAME = "Override Test"

        @register_device("lumi.test.other")
        class OtherDevice(Device):
            MODEL = "lumi.test.other"
    """)
    with pytest.raises(RuntimeError, match="override class.*not in MODELS"):
        registry.get_device_class("anything")


def test_validation_whitelabels_key_not_in_models(monkeypatch, tmp_path):
    """WHITELABELS key not in MODELS raises RuntimeError."""
    _synth_package(monkeypatch, tmp_path, "synth_bad_whitelabel", """
        MODELS = ("lumi.test.base",)
        MANUFACTURER = "Aqara"
        DISPLAY_NAME = "WL Test"
        WHITELABELS = {"lumi.test.other": "Other Brand"}
    """)
    with pytest.raises(RuntimeError, match="WHITELABELS key.*not in MODELS"):
        registry.get_device_class("anything")


def test_validation_empty_package(monkeypatch, tmp_path):
    """An empty package (no override class, no data) raises RuntimeError."""
    _synth_package(monkeypatch, tmp_path, "synth_empty_pkg", """
        # No MODELS, no TRAITS, no class, nothing.
    """)
    with pytest.raises(RuntimeError, match="empty package"):
        registry.get_device_class("anything")


# ---------------------------------------------------------------------------
# reset_for_tests clears all surviving indices.
# ---------------------------------------------------------------------------


def test_reset_for_tests_clears_all_indices(monkeypatch, tmp_path):
    """reset_for_tests clears _TRAIT_INDEX and _DISPLAY_INDEX."""
    from custom_components.aqara_lanlink.device.traits import TraitSpec

    _synth_package(monkeypatch, tmp_path, "synth_for_reset", """
        from custom_components.aqara_lanlink.device.traits import TraitSpec
        MODELS = ("lumi.test.reset",)
        MANUFACTURER = "Aqara"
        DISPLAY_NAME = "Reset Test"
        TRAITS = {"1.7.85": TraitSpec(id="1.7.85", name="motion")}
    """)
    # Trigger discovery to populate indices.
    registry.get_device_class("lumi.test.reset")

    assert len(registry._TRAIT_INDEX) > 0
    assert len(registry._DISPLAY_INDEX) > 0

    registry.reset_for_tests()

    assert len(registry._TRAIT_INDEX) == 0
    assert len(registry._DISPLAY_INDEX) == 0


# ---------------------------------------------------------------------------
# Positive test: valid package populates all three indices correctly.
# ---------------------------------------------------------------------------


def test_valid_package_populates_indices(monkeypatch, tmp_path):
    """A valid package with TRAITS and WHITELABELS populates all indices."""
    from custom_components.aqara_lanlink.device.traits import TraitSpec

    _synth_package(monkeypatch, tmp_path, "synth_full_pkg", """
        from custom_components.aqara_lanlink.device.traits import TraitSpec
        MODELS = ("lumi.test.primary", "lumi.test.variant")
        MANUFACTURER = "Aqara"
        DISPLAY_NAME = "Full Test Device"
        WHITELABELS = {
            "lumi.test.primary": "Primary Brand",
            "lumi.test.variant": "Variant Brand",
        }
        TRAITS = {
            "1.7.85": TraitSpec(id="1.7.85", name="motion"),
            "8.0.2259": TraitSpec(id="8.0.2259", name="mode"),
        }
    """)

    registry.get_device_class("anything")

    # _DISPLAY_INDEX: one entry per model in MODELS.
    # WHITELABELS override the display name for specific models.
    assert ("lumi.test.primary") in registry._DISPLAY_INDEX
    assert ("lumi.test.variant") in registry._DISPLAY_INDEX
    meta_primary = registry._DISPLAY_INDEX["lumi.test.primary"]
    assert meta_primary.manufacturer == "Aqara"
    assert meta_primary.display_name == "Primary Brand"
    meta_variant = registry._DISPLAY_INDEX["lumi.test.variant"]
    assert meta_variant.manufacturer == "Aqara"
    assert meta_variant.display_name == "Variant Brand"

    # _TRAIT_INDEX: one entry per (model, trait_id) pair for each model in MODELS.
    assert ("lumi.test.primary", "1.7.85") in registry._TRAIT_INDEX
    assert ("lumi.test.primary", "8.0.2259") in registry._TRAIT_INDEX
    assert ("lumi.test.variant", "1.7.85") in registry._TRAIT_INDEX
    assert ("lumi.test.variant", "8.0.2259") in registry._TRAIT_INDEX

    spec = registry._TRAIT_INDEX[("lumi.test.primary", "1.7.85")]
    assert spec.name == "motion"


# ---------------------------------------------------------------------------
# _discovered flag is reset on validation failure (no half-initialised state).
# ---------------------------------------------------------------------------


def test_discovered_flag_reset_on_validation_error(monkeypatch, tmp_path):
    """A RuntimeError mid-discovery does not leave _discovered permanently True."""
    _synth_package(monkeypatch, tmp_path, "synth_invalid_for_flag", """
        MODELS = ()
    """)
    with pytest.raises(RuntimeError):
        registry.get_device_class("anything")

    # _discovered must be False so a second call can retry (or at least not
    # silently return stale empty results forever).
    assert registry._discovered is False


def test_retry_after_validation_failure_no_stale_index(monkeypatch, tmp_path):
    """After a failed discovery pass, a retry must not trip the cross-package
    duplicate-model check on data left over from the first (failed) pass.

    Setup: two packages -- the first is valid, the second is invalid.  The
    first call to get_device_class raises on the second package.  After fixing
    (removing) the second package, a retry must re-index the first package
    cleanly with no spurious "model already claimed" error.
    """
    # First package: valid.
    _synth_package(monkeypatch, tmp_path, "synth_retry_good", """
        MODELS = ("lumi.test.retry",)
        MANUFACTURER = "Aqara"
        DISPLAY_NAME = "Retry Good"
    """)
    # Second package: invalid (empty MODELS tuple triggers RuntimeError).
    _synth_package(monkeypatch, tmp_path, "synth_retry_bad", """
        MODELS = ()
    """)

    # First attempt: raises because of the invalid second package.
    with pytest.raises(RuntimeError):
        registry.get_device_class("lumi.test.retry")

    # Confirm _discovered is False and indices were cleared.
    assert registry._discovered is False
    assert len(registry._DISPLAY_INDEX) == 0
    assert len(registry._TRAIT_INDEX) == 0

    # Remove the invalid subpackage from sys.modules and from the filesystem
    # so the second discovery pass does not find it.
    bad_mod_name = (
        "custom_components.aqara_lanlink.device.models.synth_retry_bad"
    )
    sys.modules.pop(bad_mod_name, None)
    import shutil
    shutil.rmtree(str(tmp_path / "synth_retry_bad"), ignore_errors=True)

    # Second attempt: must succeed without any "model already claimed" error.
    cls = registry.get_device_class("lumi.test.retry")
    # The first package had no override class, so None is the expected return.
    assert cls is None
    # The valid package's model must now appear in _DISPLAY_INDEX.
    assert "lumi.test.retry" in registry._DISPLAY_INDEX
    assert registry._discovered is True


@pytest.mark.xfail(
    reason="data lives in TraitSpec.wire_path after Chunk 3 regeneration",
)
def test_known_paths_indexed_on_discovery(monkeypatch, tmp_path):
    """wire_path lands on the per-trait TraitSpec entry in _TRAIT_INDEX."""
    _synth_package(monkeypatch, tmp_path, "synth_kp_a", """
        from custom_components.aqara_lanlink.device.traits import TraitSpec
        MODELS = ("lumi.fake.kp",)
        MANUFACTURER = "Aqara"
        DISPLAY_NAME = "Fake KP"
        TRAITS = {"0.3.85": TraitSpec(id="0.3.85", name="kp")}
        KNOWN_PATHS = {"0.3.85": "6.154.32989"}
    """)
    registry.get_device_class("anything")  # triggers discovery
    spec = registry._TRAIT_INDEX[("lumi.fake.kp", "0.3.85")]
    assert spec.wire_path == "6.154.32989"


@pytest.mark.xfail(
    reason="data lives in TraitSpec.wire_path after Chunk 3 regeneration",
)
def test_known_paths_multi_model_fans_out(monkeypatch, tmp_path):
    """Multi-model packages share the TraitSpec.wire_path value per model."""
    _synth_package(monkeypatch, tmp_path, "synth_kp_multi", """
        from custom_components.aqara_lanlink.device.traits import TraitSpec
        MODELS = ("lumi.a", "lumi.b")
        MANUFACTURER = "Aqara"
        DISPLAY_NAME = "Multi"
        TRAITS = {"0.3.85": TraitSpec(id="0.3.85", name="kp")}
        KNOWN_PATHS = {"0.3.85": "6.154.32989"}
    """)
    registry.get_device_class("anything")
    assert registry._TRAIT_INDEX[("lumi.a", "0.3.85")].wire_path == "6.154.32989"
    assert registry._TRAIT_INDEX[("lumi.b", "0.3.85")].wire_path == "6.154.32989"


def test_candidate_traits_attribute_ignored_during_discovery(monkeypatch, tmp_path):
    """CANDIDATE_TRAITS is documentation-only; registry does not index it
    nor raise on malformed shapes. Future chunks may emit it from the
    generator; this test pins the runtime-ignores-it contract."""
    _synth_package(monkeypatch, tmp_path, "synth_candidates", """
        MODELS = ("lumi.fake.candidates",)
        MANUFACTURER = "Aqara"
        DISPLAY_NAME = "Has candidates"
        CANDIDATE_TRAITS = {"14.211.85": "anything; not even a real TraitSpec"}
    """)
    registry.get_device_class("anything")  # must not raise
    # Nothing about candidates should land in the trait index:
    assert "14.211.85" not in registry._TRAIT_INDEX


def test_candidate_traits_malformed_does_not_raise(monkeypatch, tmp_path):
    """A garbage CANDIDATE_TRAITS value must NOT raise. Documentation
    typos shouldn't break the integration."""
    _synth_package(monkeypatch, tmp_path, "synth_candidates_bad", """
        MODELS = ("lumi.fake.cbad",)
        MANUFACTURER = "Aqara"
        DISPLAY_NAME = "Bad candidates"
        CANDIDATE_TRAITS = 42  # not a dict
    """)
    registry.get_device_class("anything")  # must not raise


def test_path_and_enum_label_indexes_removed():
    """v3d-1: _PATH_INDEX and _ENUM_LABEL_INDEX no longer exist."""
    from custom_components.aqara_lanlink.device import registry

    assert not hasattr(registry, "_PATH_INDEX")
    assert not hasattr(registry, "_ENUM_LABEL_INDEX")


def test_fake_known_paths_silently_ignored(monkeypatch, tmp_path):
    """Legacy fake.KNOWN_PATHS / fake.ENUM_LABELS on a fake module is ignored.

    After v3d-1 the registry no longer reads these attributes, so setting
    them on a fake module has no effect (no error, no side-effects).
    """
    import sys
    import types
    from custom_components.aqara_lanlink.device import registry, traits

    registry.reset_for_tests()
    fake = types.ModuleType("fake_pkg")
    fake.MODELS = ("lumi.fake.0",)
    fake.MANUFACTURER = "X"
    fake.DISPLAY_NAME = "X"
    fake.TRAITS = {"0.0.0": traits.TraitSpec(id="0.0.0", name="x")}
    fake.KNOWN_PATHS = {"0.0.0": "ignored.path"}
    fake.ENUM_LABELS = {"0.0.0": {"0": "ignored"}}
    registry._index_package(fake, "fake_pkg")

    # _TRAIT_INDEX still populated
    assert ("lumi.fake.0", "0.0.0") in registry._TRAIT_INDEX


def test_no_model_package_emits_candidate_traits():
    """v3d-2b: CANDIDATE_TRAITS is gone from all auto-generated packages.

    Regression guard against a future regen reintroducing the placeholder
    block. Backward-compat tests above still pin that the runtime IGNORES
    CANDIDATE_TRAITS if a hand-authored package carries one, but no
    auto-generated package should emit it.
    """
    import pkgutil

    from custom_components.aqara_lanlink.device import models as models_pkg

    for module_info in pkgutil.iter_modules(models_pkg.__path__):
        mod = __import__(
            f"custom_components.aqara_lanlink.device.models.{module_info.name}",
            fromlist=["__name__"],
        )
        assert not hasattr(mod, "CANDIDATE_TRAITS"), (
            f"{module_info.name} still has CANDIDATE_TRAITS; "
            "v3d-2b regen incomplete"
        )


# ---------------------------------------------------------------------------
# Camera fleet test removed during V3 cutover triage (Task 31).
#
# The deleted test (`test_camera_model_resolves_to_camera_device`) asserted
# that every camera model string resolves to a registered CameraDevice
# subclass via @register_device. That contract is V1-shape: V3 catalogue
# regen (commit c2d133f) replaced per-model packages with auto-generated
# data.json shells that no longer register a Device class. Re-establishing
# camera-model dispatch in the V3 world is a separate piece of work --
# either by emitting a Camera deviceType into endpoints_for_model() or by
# wiring CameraDevice via the device_types/camera composer -- and that
# work is out of scope for the cutover triage. The test will be rewritten
# under that future work item to assert the V3 camera contract instead.
# ---------------------------------------------------------------------------
