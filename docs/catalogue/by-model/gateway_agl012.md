# lumi.gateway.agl012 -- Hub M300

| Field | Value |
|---|---|
| Manufacturer | Aqara |
| Regions | CN, EU, US, unknown |
| Bundle IDs | lumi.gateway.agl012 |

## Supported traits

Surfaced to Home Assistant as enabled-by-default entities. Composer fusion (Light / Doorbell / etc.) may consolidate several of these into one HA entity.

- `1.172.33039` -- `EcosystemInformation.SupportedEcosystem` (Supported ecosystem)
- `1.172.33040` -- `EcosystemInformation.BoundEcosystem` (Bound ecosystem)
- `1.172.33041` -- `EcosystemInformation.MagicPairPayload` (Magic pair payload)
- `1.172.33042` -- `EcosystemInformation.HomeKitPayload` (Home kit payload)
- `1.172.33043` -- `EcosystemInformation.MatterPayload` (Matter payload)
- `1.175.20138` -- `GeneralConfiguration.ResetMode` (Reset mode)
- `1.175.20211` -- `GeneralConfiguration.VoiceLanguage` (Voice language)
- `1.175.20297` -- `GeneralConfiguration.TimeZone` (Time zone)
- `1.175.20305` -- `GeneralConfiguration.HubArkTechEnable` (Hub ark tech enable)
- `1.175.20482` -- `GeneralConfiguration.UsbDataCommunicationEnable` (Usb data communication enable)
- `1.176.20227` -- `GeneralDiagnostics.AntiDeletionLevelSetting` (Anti deletion level setting)
- `1.176.20298` -- `GeneralDiagnostics.AntiDeletionSetting` (Anti deletion setting)
- `1.176.20299` -- `GeneralDiagnostics.AntiDeletionNotification` (Anti deletion notification)
- `1.248.20302` -- `NetworkManagement.RemoteProxyAccessEnable` (Remote proxy access enable)
- `1.248.20303` -- `NetworkManagement.InternetAccessAddress` (Internet access address)
- `1.248.20304` -- `NetworkManagement.LanAccessAddress` (Lan access address)
- `2.146.32960` -- `Speaker.Volume`
- `2.167.20309` -- `BridgeConfiguration.SupportedHubCapability` (Supported hub capability)
- `2.167.33018` -- `BridgeConfiguration.BridgedNodeList` (Bridged node list)
- `2.183.33118` -- `MatterBridgeCommissioning.MatterPairingSwitch` (Matter pairing switch)
- `2.183.33119` -- `MatterBridgeCommissioning.MatterDACState` (Matter DAC state)
- `2.183.33120` -- `MatterBridgeCommissioning.MatterBindState` (Matter bind state)
- `2.183.33121` -- `MatterBridgeCommissioning.MatterOnboardingPayload` (Matter onboarding payload)
- `2.183.33122` -- `MatterBridgeCommissioning.MatterManufacturerList` (Matter manufacturer list)
- `2.183.33123` -- `MatterBridgeCommissioning.MatterPairingState` (Matter pairing state)
- `2.183.33124` -- `MatterBridgeCommissioning.MatterBridgeSupported` (Matter bridge supported)
- `2.256.20385` -- `Ringtone.RingtonePlayIndex` (Ringtone play index)
- `2.256.20386` -- `Ringtone.RingtonePlayVolume` (Ringtone play volume)
- `2.256.20387` -- `Ringtone.RingtonePlayStatus` (Ringtone play status)
- `2.256.20388` -- `Ringtone.RingtonePlayDuration` (Ringtone play duration)
- `3.132.32920` -- `Output.OnOff` (On off)
- `3.141.32947` -- `HeaterCooler.HeaterCoolerMode` (Heater cooler mode)
- `3.141.32948` -- `HeaterCooler.HeatingTemperature` (Heating temperature)
- `3.141.32949` -- `HeaterCooler.CoolingTemperature` (Cooling temperature)
- `3.141.32952` -- `HeaterCooler.CurrentTemperature` (Current temperature)
- `3.141.32953` -- `HeaterCooler.CurrentHumidity` (Current humidity)
- `3.142.32950` -- `FanControl.FanMode` (Fan mode)
- `3.142.32951` -- `FanControl.RockSetting` (Rock setting)
- `4.139.32940` -- `IRControl.IRType` (IR type)
- `4.139.32972` -- `IRControl.IRKey` (IR key)

## Diagnostic traits

Surfaced as HA entities under the `diagnostic` category, default-disabled. Enable manually for debugging visibility.

- `0.128.20306` -- `BasicInformation.HubConnectionStatus` (Hub connection status)
- `1.171.20118` -- `NetworkCommissioning.WiFiChannel` (Wi fi channel)
- `1.171.20119` -- `NetworkCommissioning.WiFiRSSI` (Wi fi RSSI)
- `1.171.20390` -- `NetworkCommissioning.ChildDeviceProvisioningState` (Child device provisioning state)

## Press-to-trigger (Button) traits

_(none)_

## Dropped traits

Excluded from the integration's runtime catalogue by `trait_policy.py`. They exist in Aqara's V3 spec but produce no Home Assistant entities (administrative chatter, network plumbing, static identifiers already exposed via HA's device registry, etc.). Grouped by reason:

### Diagnostic noise (reboot/debug)

- `1.176.20009` -- `GeneralDiagnostics.DebugInfo` (Debug info)
- `1.176.20010` -- `GeneralDiagnostics.NetworkInterfaces` (Network interfaces)
- `1.176.33096` -- `GeneralDiagnostics.RebootCount` (Reboot count)
- `1.176.33097` -- `GeneralDiagnostics.RebootReason` (Reboot reason)

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
- `3.130.33012` -- `EndpointLabel.EndpointStatistics` (Endpoint statistics)
- `4.130.32913` -- `EndpointLabel.EndpointName` (Endpoint name)
- `4.130.32915` -- `EndpointLabel.EndpointVisibility` (Endpoint visibility)
- `4.130.32919` -- `EndpointLabel.EndpointIcon` (Endpoint icon)
- `4.130.33012` -- `EndpointLabel.EndpointStatistics` (Endpoint statistics)

### Endpoint topology metadata (HA-managed)

- `0.129.32906` -- `Descriptor.SupportedEndpointDynamic` (Supported endpoint dynamic)
- `0.129.32907` -- `Descriptor.EndpointDynamicCount` (Endpoint dynamic count)
- `0.129.32908` -- `Descriptor.EndpointCount` (Endpoint count)
- `0.129.32910` -- `Descriptor.EndpointDeviceTypes` (Endpoint device types)
- `0.129.32911` -- `Descriptor.EndpointFunctions` (Endpoint functions)
- `0.129.33013` -- `Descriptor.EndpointArrayDynamic` (Endpoint array dynamic)

### Momentary counter / non-actionable

- `1.131.32916` -- `Identify.IdentifyTime` (Identify time)
- `1.131.32917` -- `Identify.IdentifyType` (Identify type)

### Protocol-internal commissioning state

- `1.171.20116` -- `NetworkCommissioning.ZigbeeAuthRandcode` (Zigbee auth randcode)
- `1.171.20117` -- `NetworkCommissioning.ZigbeeAuthRandcodeResponse` (Zigbee auth randcode response)
- `1.171.20308` -- `NetworkCommissioning.MagicPairKeychain` (Magic pair keychain)
- `1.171.33027` -- `NetworkCommissioning.SupportedNetwork` (Supported network)
- `1.171.33028` -- `NetworkCommissioning.WiFiDiagnostics` (Wi fi diagnostics)
- `1.171.33029` -- `NetworkCommissioning.ThreadDiagnostics` (Thread diagnostics)
- `1.171.33030` -- `NetworkCommissioning.ZigbeeDiagnostics` (Zigbee diagnostics)

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
- `0.128.33056` -- `BasicInformation.SupportedMatterRoles` (Supported matter roles)

### Thread protocol plumbing

- `2.181.20089` -- `ThreadNetworkDiagnostics.ThreadTopologyData` (Thread topology data)
- `2.181.20483` -- `ThreadNetworkDiagnostics.ThreadPingResult` (Thread ping result)

### Zigbee protocol plumbing

- `2.205.20113` -- `ZigbeeNetworkDiagnostics.ZigbeeFrameCounter` (Zigbee frame counter)
- `2.205.20300` -- `ZigbeeNetworkDiagnostics.ZigbeeCoordNetworkKey` (Zigbee coord network key)
- `2.205.20301` -- `ZigbeeNetworkDiagnostics.ZigbeeCoordMac` (Zigbee coord mac)
- `2.205.20307` -- `ZigbeeNetworkDiagnostics.ZigbeeKeySeqNumber` (Zigbee key seq number)
- `2.205.20391` -- `ZigbeeNetworkDiagnostics.ZigbeeExtendedPANID` (Zigbee extended PANID)
- `2.205.33098` -- `ZigbeeNetworkDiagnostics.Channel`
- `2.205.33099` -- `ZigbeeNetworkDiagnostics.PANID`

---

_V3 spec totals: 103 traits (40 supported, 4 diagnostic, 0 button, 59 dropped). Generated by `tools/render_catalogue_docs.py`._
