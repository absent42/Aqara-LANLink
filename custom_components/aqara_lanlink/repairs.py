"""Repairs platform for Aqara LANLink.

Three Repair flows:

- `scan_review_<entry_id>_<did>` - the user clicks Fix on a discovered
  gap report; the multi-step flow lets them pick which traits to
  accept. Accepted traits go to the local overlay; the entry reloads.
- `scan_failure_<entry_id>_<did>` - the cloud scan failed; informational
  only.
- `candidate_paths_<entry_id>` - LANLink push observed a wire path
  absent from catalogue + overlay; one issue per entry summarising every
  affected (model, path); prompts the user to run scan_device.

The `scan_*` ids carry both entry_id and did so deletion is precise;
`candidate_paths_*` carries entry_id only because the candidate list
collapses across all devices on the entry.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir, selector

from .const import DOMAIN
from .device.traits import TraitSpec

_LOGGER = logging.getLogger(__name__)


class _ScanReviewFlow(RepairsFlow):
    """Per-trait review-and-accept flow for a scan_device gap report."""

    def __init__(
        self,
        entry_id: str,
        did: str,
        model: str,
        report: list[dict],
    ) -> None:
        self._entry_id = entry_id
        self._did = did
        self._model = model
        self._report = report
        # Maps option-key (the pid) to its dict-shaped GapEntry payload.
        self._by_pid = {row["pid"]: row for row in report}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self.async_step_select()

    async def async_step_select(
        self, user_input: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if user_input is None:
            options = [
                selector.SelectOptionDict(
                    value=row["pid"],
                    label=self._format_option_label(row),
                )
                for row in self._report
            ]
            # Default selects all entries to reduce clicks; user can untick
            # any they don't want to accept.
            schema = vol.Schema({
                vol.Optional(
                    "selected",
                    default=[row["pid"] for row in self._report],
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    ),
                ),
            })
            return self.async_show_form(
                step_id="select",
                data_schema=schema,
                description_placeholders={
                    "model": self._model,
                    "did": self._did,
                    "count": str(len(self._report)),
                    "details": self._format_details(),
                },
            )

        selected = user_input.get("selected") or []
        await self._accept(selected)
        ir.async_delete_issue(
            self.hass, DOMAIN, f"scan_review_{self._entry_id}_{self._did}",
        )
        await self.hass.config_entries.async_reload(self._entry_id)
        return self.async_create_entry(title="", data={})

    async def _accept(self, selected_pids: list[str]) -> None:
        """Write selected gap entries into the overlay."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return
        runtime = entry.runtime_data
        overlay = runtime.overlay
        now = datetime.now(timezone.utc)
        for pid in selected_pids:
            row = self._by_pid.get(pid)
            if row is None:
                continue
            spec_dict = row["proposed_spec"]
            try:
                spec = TraitSpec(
                    id=spec_dict["id"],
                    name=spec_dict["name"],
                    wire_path=spec_dict.get("wire_path"),
                    data_type=spec_dict.get("data_type", "unknown"),
                    unit=spec_dict.get("unit"),
                    enum_values=spec_dict.get("enum_values"),
                    platform=spec_dict.get("platform"),
                    device_class=spec_dict.get("device_class"),
                    readable=bool(spec_dict.get("readable", True)),
                    writable=bool(spec_dict.get("writable", False)),
                    trait_id=spec_dict.get("trait_id"),
                )
            except (RuntimeError, KeyError, TypeError, ValueError) as exc:
                _LOGGER.warning(
                    "scan_review: dropping accepted row pid=%s: %s",
                    pid, exc,
                )
                continue
            overlay.set_trait(
                model=self._model, pid=pid, spec=spec,
                discovered_from=row.get("discovered_from", "cloud_scan"),
                discovered_at=now,
            )
        await runtime.overlay_store.async_write(overlay)

    def _format_option_label(self, row: dict) -> str:
        spec = row["proposed_spec"]
        bits = [
            row["pid"],
            spec.get("name") or "(no name)",
            spec.get("platform") or "?",
        ]
        if spec.get("device_class"):
            bits.append(spec["device_class"])
        if row.get("cloud_value") is not None:
            bits.append(f"value={row['cloud_value']}")
        return " / ".join(bits)

    def _format_details(self) -> str:
        lines = [f"- `{r['pid']}` -> `{r['wire_path']}` "
                 f"({r['proposed_spec'].get('name')})"
                 for r in self._report]
        return "\n".join(lines)


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Build the appropriate fix flow for an issue id."""
    if issue_id.startswith("scan_review_") and data:
        return _ScanReviewFlow(
            entry_id=data["entry_id"],
            did=data["did"],
            model=data["model"],
            report=data.get("report") or [],
        )
    # candidate_paths_* and any other Repair issue without a custom flow
    # fall through to the confirm-only flow. For candidate_paths the
    # integration cannot guess which device to scan from a multi-device
    # candidate list; the user dismisses and runs scan_device explicitly.
    return ConfirmRepairFlow()
