# lumi.airrtc.aeu001 -- Floor Heating Thermostat W500

| Field | Value |
|---|---|
| Manufacturer | Aqara |
| Regions | EU, US |
| Bundle IDs | aqara.thermostat.floor |

## Supported traits

Surfaced to Home Assistant as enabled-by-default entities. Composer fusion (Light / Doorbell / etc.) may consolidate several of these into one HA entity.

- `1.140.20029` -- `EnergyManagement.CircuitCurrent` (Circuit current)
- `1.140.32945` -- `EnergyManagement.CurrentPower` (Current power)
- `1.140.32946` -- `EnergyManagement.CumulativeEnergyConsumption` (Cumulative energy consumption)
- `2.141.32947` -- `HeaterCooler.HeaterCoolerMode` (Heater cooler mode)
- `2.141.32948` -- `HeaterCooler.HeatingTemperature` (Heating temperature)
- `2.141.32952` -- `HeaterCooler.CurrentTemperature` (Current temperature)

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
- `0.130.33012` -- `EndpointLabel.EndpointStatistics` (Endpoint statistics)
- `0.130.33016` -- `EndpointLabel.EndpointRoomName` (Endpoint room name)
- `1.130.32913` -- `EndpointLabel.EndpointName` (Endpoint name)
- `1.130.32915` -- `EndpointLabel.EndpointVisibility` (Endpoint visibility)
- `1.130.33012` -- `EndpointLabel.EndpointStatistics` (Endpoint statistics)
- `2.130.32913` -- `EndpointLabel.EndpointName` (Endpoint name)
- `2.130.32915` -- `EndpointLabel.EndpointVisibility` (Endpoint visibility)
- `2.130.32919` -- `EndpointLabel.EndpointIcon` (Endpoint icon)
- `2.130.33012` -- `EndpointLabel.EndpointStatistics` (Endpoint statistics)

### Endpoint topology metadata (HA-managed)

- `0.129.32906` -- `Descriptor.SupportedEndpointDynamic` (Supported endpoint dynamic)
- `0.129.32907` -- `Descriptor.EndpointDynamicCount` (Endpoint dynamic count)
- `0.129.32908` -- `Descriptor.EndpointCount` (Endpoint count)
- `0.129.32909` -- `Descriptor.EndpointArray` (Endpoint array)
- `0.129.32910` -- `Descriptor.EndpointDeviceTypes` (Endpoint device types)
- `0.129.32911` -- `Descriptor.EndpointFunctions` (Endpoint functions)
- `0.129.33013` -- `Descriptor.EndpointArrayDynamic` (Endpoint array dynamic)

### Momentary counter / non-actionable

- `1.131.32916` -- `Identify.IdentifyTime` (Identify time)
- `1.131.32917` -- `Identify.IdentifyType` (Identify type)

### Static identifier (in HA device registry)

- `0.128.20095` -- `BasicInformation.ChipTemperature` (Chip temperature)
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
- `0.128.33046` -- `BasicInformation.HardwareVersionString` (Hardware version string)
- `0.128.33047` -- `BasicInformation.FirmwareRevisionString` (Firmware revision string)

### Zigbee protocol plumbing

- `1.205.20100` -- `ZigbeeNetworkDiagnostics.ZigbeeNextHopMac` (Zigbee next hop mac)
- `1.205.20110` -- `ZigbeeNetworkDiagnostics.ZigbeeNeighborTableInfo` (Zigbee neighbor table info)
- `1.205.20111` -- `ZigbeeNetworkDiagnostics.ZigbeeAvgSuccessfulResendCount` (Zigbee avg successful resend count)
- `1.205.20112` -- `ZigbeeNetworkDiagnostics.ZigbeeFailedMessageInOneHour` (Zigbee failed message in one hour)
- `1.205.20114` -- `ZigbeeNetworkDiagnostics.ZigbeeResentMessageInOneHour` (Zigbee resent message in one hour)
- `1.205.20115` -- `ZigbeeNetworkDiagnostics.ZigbeeTotalMessageInOneHour` (Zigbee total message in one hour)
- `1.205.20123` -- `ZigbeeNetworkDiagnostics.APSRetransmissionCount` (APS retransmission count)
- `1.205.20124` -- `ZigbeeNetworkDiagnostics.CCAMode` (CCA mode)
- `1.205.20125` -- `ZigbeeNetworkDiagnostics.CurOperationMode` (Cur operation mode)
- `1.205.20135` -- `ZigbeeNetworkDiagnostics.RoutingAgingCountThreshold` (Routing aging count threshold)
- `1.205.20136` -- `ZigbeeNetworkDiagnostics.NWKRetransmissionCount` (NWK retransmission count)
- `1.205.20137` -- `ZigbeeNetworkDiagnostics.PreOperationMode` (Pre operation mode)
- `1.205.33098` -- `ZigbeeNetworkDiagnostics.Channel`
- `1.205.33107` -- `ZigbeeNetworkDiagnostics.LQI`

---

_V3 spec totals: 55 traits (6 supported, 0 diagnostic, 0 button, 49 dropped). Generated by `tools/render_catalogue_docs.py`._
