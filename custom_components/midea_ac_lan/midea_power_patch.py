"""Model-specific power parsing fixes for midea-local."""

from __future__ import annotations

import json
import logging
from typing import Any

from midealocal.devices.ac.message import XC1MessageBody

_LOGGER = logging.getLogger(__name__)

Q1D_MODEL = "00000Q1D"
Q1D_SUBTYPE = 524
Q1D_POWER_ANALYSIS_METHOD = 101

_PATCHED = False
_ORIGINAL_PARSE_POWER = XC1MessageBody.parse_power
_ORIGINAL_PARSE_CONSUMPTION = XC1MessageBody.parse_consumption


def install_q1d_realtime_power_patch() -> None:
    """Teach midea-local the Q1D/524 mixed energy format.

    The appliance reports energy counters as BCD, but realtime power as a
    24-bit big-endian integer in 0.1 W units. The upstream midea-local
    power_analysis_method applies one format to both fields, so this fork uses
    a private method id and preserves the normal parser for every other device.
    """
    global _PATCHED  # noqa: PLW0603

    if _PATCHED:
        return

    def parse_power(
        _cls: type[XC1MessageBody],
        analysis_method: int,
        databytes: bytearray,
    ) -> float:
        if analysis_method == Q1D_POWER_ANALYSIS_METHOD:
            return int.from_bytes(bytes(databytes), "big") / 10
        return _ORIGINAL_PARSE_POWER(analysis_method, databytes)

    def parse_consumption(
        _cls: type[XC1MessageBody],
        analysis_method: int,
        databytes: bytearray,
    ) -> float:
        if analysis_method == Q1D_POWER_ANALYSIS_METHOD:
            return _ORIGINAL_PARSE_CONSUMPTION(1, databytes)
        return _ORIGINAL_PARSE_CONSUMPTION(analysis_method, databytes)

    XC1MessageBody.parse_power = classmethod(parse_power)  # type: ignore[method-assign,assignment]
    XC1MessageBody.parse_consumption = classmethod(parse_consumption)  # type: ignore[method-assign,assignment]
    _PATCHED = True


def apply_q1d_power_customize(customize: str, model: str, subtype: int) -> str:
    """Return customize JSON with the Q1D realtime power parser enabled.

    Returns:
        The original or updated customize JSON string.

    """
    try:
        subtype_int = int(subtype)
    except (TypeError, ValueError):
        return customize

    if model != Q1D_MODEL or subtype_int != Q1D_SUBTYPE:
        return customize

    try:
        params: Any = json.loads(customize) if customize else {}
    except json.JSONDecodeError:
        _LOGGER.warning(
            "Cannot apply Q1D power parser because customize is not valid JSON",
        )
        return customize

    if not isinstance(params, dict):
        _LOGGER.warning(
            "Cannot apply Q1D power parser because customize is not a JSON object",
        )
        return customize

    if params.get("power_analysis_method") == Q1D_POWER_ANALYSIS_METHOD:
        return customize

    if "power_analysis_method" in params:
        _LOGGER.info(
            "Overriding power_analysis_method=%s for Midea model %s subtype %s",
            params["power_analysis_method"],
            model,
            subtype,
        )

    params["power_analysis_method"] = Q1D_POWER_ANALYSIS_METHOD
    return json.dumps(params, separators=(",", ":"))
