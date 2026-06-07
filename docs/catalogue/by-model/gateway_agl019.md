# lumi.gateway.agl019 -- lumi.gateway.agl019

| Field | Value |
|---|---|
| Manufacturer | Aqara |
| Regions | (none) |
| Bundle IDs | (none) |

## Supported traits

Surfaced to Home Assistant as enabled-by-default entities. Composer fusion (Light / Doorbell / etc.) may consolidate several of these into one HA entity.

- `1.140.32944` -- `EnergyManagement.CurrentVoltage` (Current voltage)
- `1.140.32945` -- `EnergyManagement.CurrentPower` (Current power)
- `1.140.32946` -- `EnergyManagement.CumulativeEnergyConsumption` (Cumulative energy consumption)
- `10.135.32928` -- `Button.ButtonEvent` (Button event)
- `11.135.32928` -- `Button.ButtonEvent` (Button event)
- `12.135.32928` -- `Button.ButtonEvent` (Button event)
- `2.146.32960` -- `Speaker.Volume`
- `2.187.20022` -- `ScreenDisplay.ScreenBrightness` (Screen brightness)
- `3.145.32954` -- `MediaPlayback.CurrentPlaybackState` (Current playback state)
- `3.145.32958` -- `MediaPlayback.MediaInformation` (Media information)
- `3.145.33015` -- `MediaPlayback.LoginState` (Login state)
- `3.145.33021` -- `MediaPlayback.TargetPlaybackState` (Target playback state)
- `3.146.32960` -- `Speaker.Volume`
- `3.146.32961` -- `Speaker.Mute`
- `3.146.32962` -- `Speaker.StepValue` (Step value)
- `4.132.32920` -- `Output.OnOff` (On off)
- `4.135.32928` -- `Button.ButtonEvent` (Button event)
- `5.132.32920` -- `Output.OnOff` (On off)
- `5.135.32928` -- `Button.ButtonEvent` (Button event)
- `6.132.32920` -- `Output.OnOff` (On off)
- `6.135.32928` -- `Button.ButtonEvent` (Button event)
- `7.135.32928` -- `Button.ButtonEvent` (Button event)
- `8.135.32928` -- `Button.ButtonEvent` (Button event)
- `9.135.32928` -- `Button.ButtonEvent` (Button event)

## Diagnostic traits

Surfaced as HA entities under the `diagnostic` category, default-disabled. Enable manually for debugging visibility.

- `1.171.20118` -- `NetworkCommissioning.WiFiChannel` (Wi fi channel)
- `1.171.20119` -- `NetworkCommissioning.WiFiRSSI` (Wi fi RSSI)

## Press-to-trigger (Button) traits

_(none)_

## Dropped traits

Excluded from the integration's runtime catalogue by `trait_policy.py`. They exist in Aqara's V3 spec but produce no Home Assistant entities (administrative chatter, network plumbing, static identifiers already exposed via HA's device registry, etc.). Grouped by reason:

### Diagnostic noise (reboot/debug)

- `1.176.20009` -- `GeneralDiagnostics.DebugInfo` (Debug info)
- `1.176.33096` -- `GeneralDiagnostics.RebootCount` (Reboot count)
- `1.176.33097` -- `GeneralDiagnostics.RebootReason` (Reboot reason)

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
- `12.130.32913` -- `EndpointLabel.EndpointName` (Endpoint name)
- `12.130.32915` -- `EndpointLabel.EndpointVisibility` (Endpoint visibility)
- `12.130.32919` -- `EndpointLabel.EndpointIcon` (Endpoint icon)
- `12.130.33012` -- `EndpointLabel.EndpointStatistics` (Endpoint statistics)
- `2.130.32913` -- `EndpointLabel.EndpointName` (Endpoint name)
- `2.130.32915` -- `EndpointLabel.EndpointVisibility` (Endpoint visibility)
- `2.130.32919` -- `EndpointLabel.EndpointIcon` (Endpoint icon)
- `2.130.33012` -- `EndpointLabel.EndpointStatistics` (Endpoint statistics)
- `3.130.32913` -- `EndpointLabel.EndpointName` (Endpoint name)
- `3.130.32915` -- `EndpointLabel.EndpointVisibility` (Endpoint visibility)
- `3.130.32919` -- `EndpointLabel.EndpointIcon` (Endpoint icon)
- `3.130.33012` -- `EndpointLabel.EndpointStatistics` (Endpoint statistics)
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
- `6.130.32914` -- `EndpointLabel.EndpointRoom` (Endpoint room)
- `6.130.32915` -- `EndpointLabel.EndpointVisibility` (Endpoint visibility)
- `6.130.32919` -- `EndpointLabel.EndpointIcon` (Endpoint icon)
- `6.130.32929` -- `EndpointLabel.EndpointApplianceType` (Endpoint appliance type)
- `6.130.33012` -- `EndpointLabel.EndpointStatistics` (Endpoint statistics)
- `6.130.33016` -- `EndpointLabel.EndpointRoomName` (Endpoint room name)
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

### Protocol-internal commissioning state

- `1.171.33027` -- `NetworkCommissioning.SupportedNetwork` (Supported network)

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

- `1.205.20300` -- `ZigbeeNetworkDiagnostics.ZigbeeCoordNetworkKey` (Zigbee coord network key)
- `1.205.20301` -- `ZigbeeNetworkDiagnostics.ZigbeeCoordMac` (Zigbee coord mac)
- `1.205.20391` -- `ZigbeeNetworkDiagnostics.ZigbeeExtendedPANID` (Zigbee extended PANID)
- `1.205.33099` -- `ZigbeeNetworkDiagnostics.PANID`

---

_V3 spec totals: 111 traits (24 supported, 2 diagnostic, 0 button, 85 dropped). Generated by `tools/render_catalogue_docs.py`._
