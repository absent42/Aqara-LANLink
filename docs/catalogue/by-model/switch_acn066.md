# lumi.switch.acn066 -- Panel Switch S100

| Field | Value |
|---|---|
| Manufacturer | Aqara |
| Regions | CN, EU, US, unknown |
| Bundle IDs | lumi.switch.acn066 |

## Supported traits

Surfaced to Home Assistant as enabled-by-default entities. Composer fusion (Light / Doorbell / etc.) may consolidate several of these into one HA entity.

- `1.140.32944` -- `EnergyManagement.CurrentVoltage` (Current voltage)
- `1.140.32945` -- `EnergyManagement.CurrentPower` (Current power)
- `1.140.32946` -- `EnergyManagement.CumulativeEnergyConsumption` (Cumulative energy consumption)
- `10.135.32928` -- `Button.ButtonEvent` (Button event)
- `11.135.32928` -- `Button.ButtonEvent` (Button event)
- `3.132.32920` -- `Output.OnOff` (On off)
- `4.132.32920` -- `Output.OnOff` (On off)
- `5.132.32920` -- `Output.OnOff` (On off)
- `6.135.32928` -- `Button.ButtonEvent` (Button event)
- `7.135.32928` -- `Button.ButtonEvent` (Button event)
- `8.135.32928` -- `Button.ButtonEvent` (Button event)
- `9.135.32928` -- `Button.ButtonEvent` (Button event)

## Diagnostic traits

_(none)_

## Press-to-trigger (Button) traits

_(none)_

## Dropped traits

Excluded from the integration's runtime catalogue by `trait_policy.py`. They exist in Aqara's V3 spec but produce no Home Assistant entities (administrative chatter, network plumbing, static identifiers already exposed via HA's device registry, etc.). Grouped by reason:

### Diagnostic noise (reboot/debug)

- `1.176.33096` -- `GeneralDiagnostics.RebootCount` (Reboot count)

### Endpoint icon/name metadata (HA-managed)

- `0.130.32913` -- `EndpointLabel.EndpointName` (Endpoint name)
- `0.130.32914` -- `EndpointLabel.EndpointRoom` (Endpoint room)
- `0.130.32915` -- `EndpointLabel.EndpointVisibility` (Endpoint visibility)
- `0.130.33016` -- `EndpointLabel.EndpointRoomName` (Endpoint room name)
- `1.130.32915` -- `EndpointLabel.EndpointVisibility` (Endpoint visibility)
- `10.130.32913` -- `EndpointLabel.EndpointName` (Endpoint name)
- `10.130.32915` -- `EndpointLabel.EndpointVisibility` (Endpoint visibility)
- `10.130.32919` -- `EndpointLabel.EndpointIcon` (Endpoint icon)
- `10.130.33012` -- `EndpointLabel.EndpointStatistics` (Endpoint statistics)
- `11.130.32913` -- `EndpointLabel.EndpointName` (Endpoint name)
- `11.130.32915` -- `EndpointLabel.EndpointVisibility` (Endpoint visibility)
- `11.130.32919` -- `EndpointLabel.EndpointIcon` (Endpoint icon)
- `11.130.33012` -- `EndpointLabel.EndpointStatistics` (Endpoint statistics)
- `2.130.32913` -- `EndpointLabel.EndpointName` (Endpoint name)
- `2.130.32915` -- `EndpointLabel.EndpointVisibility` (Endpoint visibility)
- `2.130.32919` -- `EndpointLabel.EndpointIcon` (Endpoint icon)
- `2.130.33012` -- `EndpointLabel.EndpointStatistics` (Endpoint statistics)
- `3.130.32913` -- `EndpointLabel.EndpointName` (Endpoint name)
- `3.130.32914` -- `EndpointLabel.EndpointRoom` (Endpoint room)
- `3.130.32915` -- `EndpointLabel.EndpointVisibility` (Endpoint visibility)
- `3.130.32919` -- `EndpointLabel.EndpointIcon` (Endpoint icon)
- `3.130.32929` -- `EndpointLabel.EndpointApplianceType` (Endpoint appliance type)
- `3.130.33012` -- `EndpointLabel.EndpointStatistics` (Endpoint statistics)
- `3.130.33016` -- `EndpointLabel.EndpointRoomName` (Endpoint room name)
- `4.130.32913` -- `EndpointLabel.EndpointName` (Endpoint name)
- `4.130.32914` -- `EndpointLabel.EndpointRoom` (Endpoint room)
- `4.130.32915` -- `EndpointLabel.EndpointVisibility` (Endpoint visibility)
- `4.130.32919` -- `EndpointLabel.EndpointIcon` (Endpoint icon)
- `4.130.32929` -- `EndpointLabel.EndpointApplianceType` (Endpoint appliance type)
- `4.130.33012` -- `EndpointLabel.EndpointStatistics` (Endpoint statistics)
- `4.130.33016` -- `EndpointLabel.EndpointRoomName` (Endpoint room name)
- `5.130.32913` -- `EndpointLabel.EndpointName` (Endpoint name)
- `5.130.32914` -- `EndpointLabel.EndpointRoom` (Endpoint room)
- `5.130.32915` -- `EndpointLabel.EndpointVisibility` (Endpoint visibility)
- `5.130.32919` -- `EndpointLabel.EndpointIcon` (Endpoint icon)
- `5.130.32929` -- `EndpointLabel.EndpointApplianceType` (Endpoint appliance type)
- `5.130.33012` -- `EndpointLabel.EndpointStatistics` (Endpoint statistics)
- `5.130.33016` -- `EndpointLabel.EndpointRoomName` (Endpoint room name)
- `6.130.32913` -- `EndpointLabel.EndpointName` (Endpoint name)
- `6.130.32915` -- `EndpointLabel.EndpointVisibility` (Endpoint visibility)
- `6.130.32919` -- `EndpointLabel.EndpointIcon` (Endpoint icon)
- `6.130.33012` -- `EndpointLabel.EndpointStatistics` (Endpoint statistics)
- `7.130.32913` -- `EndpointLabel.EndpointName` (Endpoint name)
- `7.130.32915` -- `EndpointLabel.EndpointVisibility` (Endpoint visibility)
- `7.130.32919` -- `EndpointLabel.EndpointIcon` (Endpoint icon)
- `7.130.33012` -- `EndpointLabel.EndpointStatistics` (Endpoint statistics)
- `8.130.32913` -- `EndpointLabel.EndpointName` (Endpoint name)
- `8.130.32915` -- `EndpointLabel.EndpointVisibility` (Endpoint visibility)
- `8.130.32919` -- `EndpointLabel.EndpointIcon` (Endpoint icon)
- `8.130.33012` -- `EndpointLabel.EndpointStatistics` (Endpoint statistics)
- `9.130.32913` -- `EndpointLabel.EndpointName` (Endpoint name)
- `9.130.32915` -- `EndpointLabel.EndpointVisibility` (Endpoint visibility)
- `9.130.32919` -- `EndpointLabel.EndpointIcon` (Endpoint icon)
- `9.130.33012` -- `EndpointLabel.EndpointStatistics` (Endpoint statistics)

### Endpoint topology metadata (HA-managed)

- `0.129.32906` -- `Descriptor.SupportedEndpointDynamic` (Supported endpoint dynamic)
- `0.129.32907` -- `Descriptor.EndpointDynamicCount` (Endpoint dynamic count)
- `0.129.32908` -- `Descriptor.EndpointCount` (Endpoint count)
- `0.129.32909` -- `Descriptor.EndpointArray` (Endpoint array)
- `0.129.32910` -- `Descriptor.EndpointDeviceTypes` (Endpoint device types)
- `0.129.32911` -- `Descriptor.EndpointFunctions` (Endpoint functions)
- `0.129.32912` -- `Descriptor.SelectedEndpointDynamic` (Selected endpoint dynamic)

### Momentary counter / non-actionable

- `1.131.32916` -- `Identify.IdentifyTime` (Identify time)
- `1.131.32917` -- `Identify.IdentifyType` (Identify type)

### Static identifier (in HA device registry)

- `0.128.32896` -- `BasicInformation.FirmwareRevision` (Firmware revision)
- `0.128.32897` -- `BasicInformation.SerialNumber` (Serial number)
- `0.128.32898` -- `BasicInformation.VendorName` (Vendor name)
- `0.128.32899` -- `BasicInformation.VendorID` (Vendor ID)
- `0.128.32900` -- `BasicInformation.ProductName` (Product name)
- `0.128.32901` -- `BasicInformation.Reachable`
- `0.128.32902` -- `BasicInformation.HardwareVersion` (Hardware version)
- `0.128.32903` -- `BasicInformation.Location`
- `0.128.32904` -- `BasicInformation.Mac`
- `0.128.32905` -- `BasicInformation.DeviceID` (Device ID)

### Zigbee protocol plumbing

- `1.205.20100` -- `ZigbeeNetworkDiagnostics.ZigbeeNextHopMac` (Zigbee next hop mac)
- `1.205.20110` -- `ZigbeeNetworkDiagnostics.ZigbeeNeighborTableInfo` (Zigbee neighbor table info)
- `1.205.33107` -- `ZigbeeNetworkDiagnostics.LQI`

---

_V3 spec totals: 89 traits (12 supported, 0 diagnostic, 0 button, 77 dropped). Generated by `tools/render_catalogue_docs.py`._
