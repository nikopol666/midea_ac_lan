"""Tests for the Midea Q1D realtime power parser workaround."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import ModuleType

from midealocal.devices.ac.message import XC1MessageBody


def load_patch_module() -> ModuleType:
    """Load the patch module without importing the Home Assistant package."""
    module_path = (
        Path(__file__).parents[1]
        / "custom_components"
        / "midea_ac_lan"
        / "midea_power_patch.py"
    )
    spec = importlib.util.spec_from_file_location("midea_power_patch", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Q1DPowerPatchTest(unittest.TestCase):
    """Validate the Q1D/524 power parser patch."""

    def test_q1d_mixed_energy_and_power_format(self) -> None:
        """Q1D/524 keeps BCD kWh counters but uses binary realtime watts."""
        patch = load_patch_module()
        patch.install_q1d_realtime_power_patch()

        samples = [
            (
                "c12101440000005800000000000000160016b700000001a9",
                0.58,
                0.16,
                581.5,
            ),
            (
                "c12101440000005900000000000000170016a30000000105",
                0.59,
                0.17,
                579.5,
            ),
            (
                "c12101440000005a000000000000001800162b0000000187",
                0.60,
                0.18,
                567.5,
            ),
        ]

        for payload, total_kwh, current_kwh, watts in samples:
            with self.subTest(payload=payload):
                parsed = XC1MessageBody(
                    bytearray.fromhex(payload),
                    patch.Q1D_POWER_ANALYSIS_METHOD,
                )

                self.assertEqual(parsed.total_energy_consumption, total_kwh)
                self.assertEqual(parsed.current_energy_consumption, current_kwh)
                self.assertEqual(parsed.realtime_power, watts)

    def test_q1d_customize_is_model_scoped(self) -> None:
        """The private parser id is only injected for the confirmed device."""
        patch = load_patch_module()

        self.assertEqual(
            patch.apply_q1d_power_customize("", patch.Q1D_MODEL, patch.Q1D_SUBTYPE),
            '{"power_analysis_method":101}',
        )
        self.assertEqual(
            patch.apply_q1d_power_customize("", "OTHER", patch.Q1D_SUBTYPE),
            "",
        )
