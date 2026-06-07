# lumi.curtain.acn010 -- Smart Curtain Motor C4

| Field | Value |
|---|---|
| Manufacturer | Aqara |
| Regions | CN, EU, US, unknown |
| Bundle IDs | lumi.curtain.acn010 |

## Supported traits

Surfaced to Home Assistant as enabled-by-default entities. Composer fusion (Light / Doorbell / etc.) may consolidate several of these into one HA entity.

- `2.137.20006` -- `WindowCovering.UnconfiguredTravelControlPermission` (Unconfigured travel control permission)
- `2.137.20031` -- `WindowCovering.MotorOperationStatus` (Motor operation status)
- `2.137.20032` -- `WindowCovering.TravelConfigured` (Travel configured)
- `2.137.20141` -- `WindowCovering.LimitPointSetting` (Limit point setting)
- `2.137.20142` -- `WindowCovering.ManualPullStartEnabled` (Manual pull start enabled)
- `2.137.20143` -- `WindowCovering.ManualPullStopEnabled` (Manual pull stop enabled)
- `2.137.20144` -- `WindowCovering.ManualPullAdaptiveSpeedEnabled` (Manual pull adaptive speed enabled)
- `2.137.20184` -- `WindowCovering.MotorSpeedSetting` (Motor speed setting)
- `2.137.20189` -- `WindowCovering.LimitPointCount` (Limit point count)
- `2.137.20190` -- `WindowCovering.ObstacleLimitMarkingBlockTime` (Obstacle limit marking block time)
- `2.137.32931` -- `WindowCovering.CurrentPositionPercentage` (Current position percentage)
- `2.137.32932` -- `WindowCovering.TargetPositionPercentage` (Target position percentage)
- `2.137.32933` -- `WindowCovering.WindowCoveringOpenOrientation` (Window covering open orientation)
- `2.137.32942` -- `WindowCovering.WindowCoveringType` (Window covering type)
- `2.137.33111` -- `WindowCovering.WindowCoveringMotorsBinding` (Window covering motors binding)
- `3.137.20006` -- `WindowCovering.UnconfiguredTravelControlPermission` (Unconfigured travel control permission)
- `3.137.20031` -- `WindowCovering.MotorOperationStatus` (Motor operation status)
- `3.137.20033` -- `WindowCovering.MotorDirectionReversed` (Motor direction reversed)
- `3.137.20141` -- `WindowCovering.LimitPointSetting` (Limit point setting)
- `3.137.20145` -- `WindowCovering.ManualPullEvent` (Manual pull event)
- `3.137.20189` -- `WindowCovering.LimitPointCount` (Limit point count)
- `3.137.32931` -- `WindowCovering.CurrentPositionPercentage` (Current position percentage)
- `3.137.32932` -- `WindowCovering.TargetPositionPercentage` (Target position percentage)
- `3.137.32933` -- `WindowCovering.WindowCoveringOpenOrientation` (Window covering open orientation)
- `3.137.32942` -- `WindowCovering.WindowCoveringType` (Window covering type)
- `3.137.33017` -- `WindowCovering.TripTime` (Trip time)
- `4.137.20006` -- `WindowCovering.UnconfiguredTravelControlPermission` (Unconfigured travel control permission)
- `4.137.20031` -- `WindowCovering.MotorOperationStatus` (Motor operation status)
- `4.137.20033` -- `WindowCovering.MotorDirectionReversed` (Motor direction reversed)
- `4.137.20141` -- `WindowCovering.LimitPointSetting` (Limit point setting)
- `4.137.20145` -- `WindowCovering.ManualPullEvent` (Manual pull event)
- `4.137.20189` -- `WindowCovering.LimitPointCount` (Limit point count)
- `4.137.32931` -- `WindowCovering.CurrentPositionPercentage` (Current position percentage)
- `4.137.32932` -- `WindowCovering.TargetPositionPercentage` (Target position percentage)
- `4.137.32933` -- `WindowCovering.WindowCoveringOpenOrientation` (Window covering open orientation)
- `4.137.32942` -- `WindowCovering.WindowCoveringType` (Window covering type)
- `4.137.33017` -- `WindowCovering.TripTime` (Trip time)

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
- `2.130.32913` -- `EndpointLabel.EndpointName` (Endpoint name)
- `2.130.32915` -- `EndpointLabel.EndpointVisibility` (Endpoint visibility)
- `2.130.32919` -- `EndpointLabel.EndpointIcon` (Endpoint icon)
- `2.130.33012` -- `EndpointLabel.EndpointStatistics` (Endpoint statistics)
- `3.130.32913` -- `EndpointLabel.EndpointName` (Endpoint name)
- `3.130.32915` -- `EndpointLabel.EndpointVisibility` (Endpoint visibility)
- `3.130.32919` -- `EndpointLabel.EndpointIcon` (Endpoint icon)
- `4.130.32913` -- `EndpointLabel.EndpointName` (Endpoint name)
- `4.130.32915` -- `EndpointLabel.EndpointVisibility` (Endpoint visibility)
- `4.130.32919` -- `EndpointLabel.EndpointIcon` (Endpoint icon)

### Endpoint topology metadata (HA-managed)

- `0.129.32906` -- `Descriptor.SupportedEndpointDynamic` (Supported endpoint dynamic)
- `0.129.32907` -- `Descriptor.EndpointDynamicCount` (Endpoint dynamic count)
- `0.129.32908` -- `Descriptor.EndpointCount` (Endpoint count)
- `0.129.32909` -- `Descriptor.EndpointArray` (Endpoint array)
- `0.129.32910` -- `Descriptor.EndpointDeviceTypes` (Endpoint device types)
- `0.129.32911` -- `Descriptor.EndpointFunctions` (Endpoint functions)
- `0.129.32912` -- `Descriptor.SelectedEndpointDynamic` (Selected endpoint dynamic)
- `0.129.33013` -- `Descriptor.EndpointArrayDynamic` (Endpoint array dynamic)
- `0.129.33110` -- `Descriptor.EndpointStructure` (Endpoint structure)

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
- `0.128.33047` -- `BasicInformation.FirmwareRevisionString` (Firmware revision string)

### Zigbee protocol plumbing

- `1.205.20100` -- `ZigbeeNetworkDiagnostics.ZigbeeNextHopMac` (Zigbee next hop mac)
- `1.205.20109` -- `ZigbeeNetworkDiagnostics.ZigbeeGeneralDiagnostics` (Zigbee general diagnostics)
- `1.205.20110` -- `ZigbeeNetworkDiagnostics.ZigbeeNeighborTableInfo` (Zigbee neighbor table info)
- `1.205.33107` -- `ZigbeeNetworkDiagnostics.LQI`

---

_V3 spec totals: 79 traits (37 supported, 0 diagnostic, 0 button, 42 dropped). Generated by `tools/render_catalogue_docs.py`._
