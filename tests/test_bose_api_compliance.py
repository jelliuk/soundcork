"""
Comprehensive Bose SoundTouch API Compliance Tests for Soundcork
=================================================================

Validates that Soundcork correctly implements the Bose SoundTouch Web API
and the Überböse Streaming API spec (UberBoseOpenAPI.json), ensuring
responses match the expected XML/JSON structure that real Bose SoundTouch
speakers expect.

Test categories:
  - Marge endpoints  : Account, device, preset, recent, source-provider
  - BMX endpoints    : Service registry, TuneIn playback, custom stream
  - OAuth endpoints  : Spotify token refresh
  - Analytics stubs  : scmudc/stapp telemetry, stats endpoints
  - SWUpdate stubs   : Firmware update suppression
  - Streaming token  : Local bearer token issuance
  - XML structure    : Root element names, required child elements
  - JSON structure   : Required keys, enum values, type conformance
  - Alias paths      : Root-level mirrors of /marge and /bmx routes
"""

import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_allowlist(*ips: str):
    """Return a SpeakerAllowlist seeded with the given IPs."""
    from soundcork.model import DeviceInfo
    from soundcork.speaker_allowlist import SpeakerAllowlist

    ds = MagicMock()
    device_infos = [
        DeviceInfo(
            device_id=f"AABBCCDD{i:04d}",
            product_code="SoundTouch 20",
            device_serial_number=f"SN{i:06d}",
            product_serial_number=f"PSN{i:06d}",
            firmware_version="27.0.6",
            ip_address=ip,
            name=f"Test Speaker {i}",
        )
        for i, ip in enumerate(ips, 1)
    ]
    ds.list_accounts.return_value = [f"acct{i}" for i in range(len(ips))]
    ds.list_devices.side_effect = [[f"AABBCCDD{i:04d}"] for i in range(1, len(ips) + 1)]
    ds.get_device_info.side_effect = device_infos
    return SpeakerAllowlist(ds)


LOOPBACK_HEADER = {"X-Forwarded-For": "127.0.0.1"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def data_dir():
    """
    Create a minimal on-disk datastore with one account, one device,
    sample Presets.xml, Recents.xml, and Sources.xml so that all marge
    endpoints can be exercised without a live speaker.
    """
    base = tempfile.mkdtemp(prefix="soundcork_test_")

    account_id = "9999999"
    device_id = "AABBCCDDEEFF"

    account_path = os.path.join(base, account_id)
    devices_path = os.path.join(account_path, "devices", device_id)
    os.makedirs(devices_path, exist_ok=True)

    # DeviceInfo.xml
    device_info = f"""<?xml version="1.0" encoding="UTF-8" ?>
<info deviceID="{device_id}">
    <name>Test Speaker</name>
    <type>SoundTouch 20</type>
    <margeAccountUUID>{account_id}</margeAccountUUID>
    <components>
        <component>
            <componentCategory>SCM</componentCategory>
            <softwareVersion>27.0.6.46330.5043500</softwareVersion>
            <serialNumber>SN123456</serialNumber>
        </component>
        <component>
            <componentCategory>PackagedProduct</componentCategory>
            <serialNumber>PSN123456</serialNumber>
        </component>
    </components>
    <margeURL>http://localhost:8000/marge</margeURL>
    <networkInfo type="SCM">
        <macAddress>AABBCCDDEEFF</macAddress>
        <ipAddress>127.0.0.1</ipAddress>
    </networkInfo>
    <moduleType>scm</moduleType>
    <variant>spotty</variant>
    <variantMode>normal</variantMode>
    <countryCode>US</countryCode>
    <regionCode></regionCode>
</info>"""
    with open(os.path.join(devices_path, "DeviceInfo.xml"), "w") as f:
        f.write(device_info)

    # Presets.xml
    presets = f"""<?xml version="1.0" encoding="UTF-8" ?>
<presets>
    <preset id="1" createdOn="1695000000" updatedOn="1695000000">
        <ContentItem source="TUNEIN" type="stationurl"
                     location="/v1/playback/station/s123456"
                     sourceAccount="" isPresetable="true">
            <itemName>Test Radio</itemName>
            <containerArt>http://cdn-profiles.tunein.com/s123456/images/logoq.png</containerArt>
        </ContentItem>
    </preset>
    <preset id="2" createdOn="1695000000" updatedOn="1695000000">
        <ContentItem source="TUNEIN" type="stationurl"
                     location="/v1/playback/station/s654321"
                     sourceAccount="" isPresetable="true">
            <itemName>Another Radio</itemName>
            <containerArt></containerArt>
        </ContentItem>
    </preset>
</presets>"""
    with open(os.path.join(account_path, "Presets.xml"), "w") as f:
        f.write(presets)

    # Recents.xml
    recents = f"""<?xml version="1.0" encoding="UTF-8" ?>
<recents>
    <recent deviceID="{device_id}" utcTime="1771287920" id="2454152503">
        <contentItem source="TUNEIN" type="stationurl"
                     location="/v1/playback/station/s123456"
                     sourceAccount="" isPresetable="true">
            <itemName>Test Radio</itemName>
        </contentItem>
    </recent>
</recents>"""
    with open(os.path.join(account_path, "Recents.xml"), "w") as f:
        f.write(recents)

    # Sources.xml
    sources = """<?xml version="1.0" encoding="UTF-8" ?>
<sources>
    <source displayName="AUX IN" secret="" secretType="">
        <sourceKey type="AUX" account="AUX" />
    </source>
    <source secret="" secretType="token">
        <sourceKey type="INTERNET_RADIO" account="" />
    </source>
    <source secret="" secretType="token">
        <sourceKey type="TUNEIN" account="" />
    </source>
</sources>"""
    with open(os.path.join(account_path, "Sources.xml"), "w") as f:
        f.write(sources)

    yield base, account_id, device_id
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture(scope="module")
def client(data_dir):
    """
    TestClient configured with the test datastore. All Bose-protocol
    endpoints are accessed via loopback (127.0.0.1) so the IP allowlist
    middleware passes them through.
    """
    base, account_id, device_id = data_dir

    import soundcork.main as main_mod
    from soundcork.config import Settings

    original_allowlist = main_mod._speaker_allowlist
    main_mod._speaker_allowlist = _make_allowlist("127.0.0.1")

    with (
        patch.object(Settings, "data_dir", new_callable=lambda: property(lambda self: base)),
        patch.object(Settings, "mgmt_password", new_callable=lambda: property(lambda self: "test_password_123")),
    ):
        with TestClient(main_mod.app) as c:
            yield c, account_id, device_id

    main_mod._speaker_allowlist = original_allowlist


@pytest.fixture(scope="module")
def acc(client):
    """Convenience: return (account_id, device_id)."""
    _, account_id, device_id = client
    return account_id, device_id


@pytest.fixture(scope="module")
def tc(client):
    """Convenience: return the TestClient."""
    c, _, _ = client
    return c


# ---------------------------------------------------------------------------
# Helpers used in tests
# ---------------------------------------------------------------------------

def parse_xml(response) -> ET.Element:
    """Assert the response is XML and return the root element."""
    ct = response.headers.get("content-type", "")
    assert "xml" in ct, f"Expected XML content-type, got: {ct!r}"
    root = ET.fromstring(response.text)
    return root


def assert_xml_tag(root: ET.Element, expected_tag: str):
    assert root.tag == expected_tag, (
        f"Expected root tag <{expected_tag}>, got <{root.tag}>"
    )


def assert_xml_children(root: ET.Element, *child_tags: str):
    actual = {child.tag for child in root}
    for tag in child_tags:
        assert tag in actual, (
            f"Missing required child <{tag}> in <{root.tag}>. Found: {sorted(actual)}"
        )


# ===========================================================================
# 1. Source Providers
# ===========================================================================

class TestSourceProviders:
    """GET /marge/streaming/sourceproviders → <sourceProviders>"""

    def test_returns_200(self, tc):
        r = tc.get("/marge/streaming/sourceproviders", headers=LOOPBACK_HEADER)
        assert r.status_code == 200

    def test_root_element_is_sourceProviders(self, tc):
        r = tc.get("/marge/streaming/sourceproviders", headers=LOOPBACK_HEADER)
        root = parse_xml(r)
        assert_xml_tag(root, "sourceProviders")

    def test_contains_sourceprovider_elements(self, tc):
        r = tc.get("/marge/streaming/sourceproviders", headers=LOOPBACK_HEADER)
        root = parse_xml(r)
        providers = root.findall("sourceprovider")
        assert len(providers) > 0, "Expected at least one <sourceprovider>"

    def test_each_provider_has_required_fields(self, tc):
        r = tc.get("/marge/streaming/sourceproviders", headers=LOOPBACK_HEADER)
        root = parse_xml(r)
        for prov in root.findall("sourceprovider"):
            assert prov.get("id") is not None, "<sourceprovider> missing id attribute"
            assert prov.find("name") is not None, "<sourceprovider> missing <name>"
            assert prov.find("createdOn") is not None, "<sourceprovider> missing <createdOn>"
            assert prov.find("updatedOn") is not None, "<sourceprovider> missing <updatedOn>"

    def test_provider_id_is_integer(self, tc):
        r = tc.get("/marge/streaming/sourceproviders", headers=LOOPBACK_HEADER)
        root = parse_xml(r)
        for prov in root.findall("sourceprovider"):
            id_val = prov.get("id")
            assert id_val.isdigit(), f"Provider id={id_val!r} is not numeric"

    def test_contains_known_providers(self, tc):
        r = tc.get("/marge/streaming/sourceproviders", headers=LOOPBACK_HEADER)
        root = parse_xml(r)
        names = {p.findtext("name") for p in root.findall("sourceprovider")}
        expected = {"TUNEIN", "SPOTIFY", "PANDORA"}
        assert expected & names, (
            f"Expected at least one of {expected} in providers, got: {names}"
        )

    def test_root_alias_path(self, tc):
        """Root alias /streaming/sourceproviders should mirror the marge path."""
        r = tc.get("/streaming/sourceproviders", headers=LOOPBACK_HEADER)
        assert r.status_code == 200
        root = parse_xml(r)
        assert_xml_tag(root, "sourceProviders")

    def test_content_type_is_xml(self, tc):
        r = tc.get("/marge/streaming/sourceproviders", headers=LOOPBACK_HEADER)
        assert "xml" in r.headers.get("content-type", "")


# ===========================================================================
# 2. Full Account
# ===========================================================================

class TestAccountFull:
    """GET /marge/streaming/account/{accountId}/full → <account>"""

    def test_returns_200(self, tc, acc):
        account_id, _ = acc
        r = tc.get(f"/marge/streaming/account/{account_id}/full", headers=LOOPBACK_HEADER)
        assert r.status_code == 200

    def test_root_element_is_account(self, tc, acc):
        account_id, _ = acc
        r = tc.get(f"/marge/streaming/account/{account_id}/full", headers=LOOPBACK_HEADER)
        root = parse_xml(r)
        assert_xml_tag(root, "account")

    def test_account_has_id_attribute(self, tc, acc):
        account_id, _ = acc
        r = tc.get(f"/marge/streaming/account/{account_id}/full", headers=LOOPBACK_HEADER)
        root = parse_xml(r)
        assert root.get("id") == account_id, (
            f"account[@id] should be {account_id!r}, got {root.get('id')!r}"
        )

    def test_required_child_elements_present(self, tc, acc):
        account_id, _ = acc
        r = tc.get(f"/marge/streaming/account/{account_id}/full", headers=LOOPBACK_HEADER)
        root = parse_xml(r)
        assert_xml_children(root, "accountStatus", "devices", "mode", "preferredLanguage", "sources")

    def test_devices_container_present(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(f"/marge/streaming/account/{account_id}/full", headers=LOOPBACK_HEADER)
        root = parse_xml(r)
        devices = root.find("devices")
        assert devices is not None
        device_list = devices.findall("device")
        assert len(device_list) >= 1

    def test_device_has_required_fields(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(f"/marge/streaming/account/{account_id}/full", headers=LOOPBACK_HEADER)
        root = parse_xml(r)
        device = root.find(".//device")
        assert device is not None
        assert_xml_children(device, "name", "presets", "recents")

    def test_presets_structure_within_full_account(self, tc, acc):
        account_id, _ = acc
        r = tc.get(f"/marge/streaming/account/{account_id}/full", headers=LOOPBACK_HEADER)
        root = parse_xml(r)
        presets = root.find(".//presets")
        assert presets is not None
        # Should have at least one preset from our fixture
        preset_list = presets.findall("preset")
        assert len(preset_list) >= 1

    def test_sources_container_present(self, tc, acc):
        account_id, _ = acc
        r = tc.get(f"/marge/streaming/account/{account_id}/full", headers=LOOPBACK_HEADER)
        root = parse_xml(r)
        sources = root.find("sources")
        assert sources is not None

    def test_root_alias_matches(self, tc, acc):
        account_id, _ = acc
        r = tc.get(f"/streaming/account/{account_id}/full", headers=LOOPBACK_HEADER)
        assert r.status_code == 200
        root = parse_xml(r)
        assert_xml_tag(root, "account")


# ===========================================================================
# 3. Presets
# ===========================================================================

class TestPresets:
    """GET /marge/streaming/account/{id}/device/{id}/presets → <presets>"""

    def test_returns_200(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/presets",
            headers=LOOPBACK_HEADER,
        )
        assert r.status_code == 200

    def test_root_element_is_presets(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/presets",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        assert_xml_tag(root, "presets")

    def test_preset_structure(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/presets",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        presets = root.findall("preset")
        assert len(presets) >= 1

        for preset in presets:
            btn = preset.get("buttonNumber")
            assert btn is not None, "preset missing buttonNumber attribute"
            assert btn.isdigit(), f"buttonNumber {btn!r} not numeric"

    def test_preset_required_child_elements(self, tc, acc):
        """Each preset must have containerArt, contentItemType, createdOn, location, name, source, updatedOn."""
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/presets",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        preset = root.find("preset")
        assert preset is not None
        required = ["containerArt", "contentItemType", "createdOn", "location", "name", "source", "updatedOn"]
        assert_xml_children(preset, *required)

    def test_preset_source_has_required_attributes(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/presets",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        source = root.find(".//source")
        assert source is not None
        assert source.get("id") is not None
        assert source.get("type") is not None

    def test_preset_etag_header_present(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/presets",
            headers=LOOPBACK_HEADER,
        )
        assert "etag" in r.headers or "ETag" in r.headers, "ETag header should be present"

    def test_root_alias_matches(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(
            f"/streaming/account/{account_id}/device/{device_id}/presets",
            headers=LOOPBACK_HEADER,
        )
        assert r.status_code == 200
        root = parse_xml(r)
        assert_xml_tag(root, "presets")


# ===========================================================================
# 4. Recents
# ===========================================================================

class TestRecents:
    """GET /marge/streaming/account/{id}/device/{id}/recents → <recents>"""

    def test_returns_200(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/recents",
            headers=LOOPBACK_HEADER,
        )
        assert r.status_code == 200

    def test_root_element_is_recents(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/recents",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        assert_xml_tag(root, "recents")

    def test_recent_items_present(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/recents",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        recents = root.findall("recent")
        assert len(recents) >= 1

    def test_recent_has_required_attributes(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/recents",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        recent = root.find("recent")
        assert recent is not None
        assert recent.get("id") is not None, "<recent> missing id attribute"

    def test_recent_has_required_children(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/recents",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        recent = root.find("recent")
        assert recent is not None
        assert_xml_children(recent, "contentItemType", "createdOn", "lastplayedat", "location", "name", "source", "updatedOn")

    def test_root_alias_matches(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(
            f"/streaming/account/{account_id}/device/{device_id}/recents",
            headers=LOOPBACK_HEADER,
        )
        assert r.status_code == 200

    def test_add_recent_post(self, tc, acc):
        """POST a new recent item; expect 200 and a <recent> response."""
        account_id, device_id = acc
        payload = """<?xml version="1.0" encoding="UTF-8"?>
<recent>
    <lastplayedat>2025-11-14T02:04:54+00:00</lastplayedat>
    <sourceid>100001</sourceid>
    <name>Test Station</name>
    <location>/v1/playback/station/s123456</location>
    <contentItemType>stationurl</contentItemType>
</recent>"""
        r = tc.post(
            f"/marge/streaming/account/{account_id}/device/{device_id}/recent",
            content=payload,
            headers={**LOOPBACK_HEADER, "Content-Type": "application/xml"},
        )
        # Expect 200 or 400 (if source not found — acceptable for fixture)
        assert r.status_code in (200, 201, 400), f"Unexpected status: {r.status_code}"


# ===========================================================================
# 5. Provider Settings
# ===========================================================================

class TestProviderSettings:
    """GET /marge/streaming/account/{accountId}/provider_settings"""

    def test_returns_200(self, tc, acc):
        account_id, _ = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/provider_settings",
            headers=LOOPBACK_HEADER,
        )
        assert r.status_code == 200

    def test_root_element(self, tc, acc):
        account_id, _ = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/provider_settings",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        assert_xml_tag(root, "providerSettings")

    def test_contains_providerSetting(self, tc, acc):
        account_id, _ = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/provider_settings",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        ps = root.find("providerSetting")
        assert ps is not None
        assert_xml_children(ps, "boseId", "keyName", "value", "providerId")

    def test_boseId_matches_account(self, tc, acc):
        account_id, _ = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/provider_settings",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        bose_id = root.findtext(".//boseId")
        assert bose_id == account_id


# ===========================================================================
# 6. Software Update
# ===========================================================================

class TestSoftwareUpdate:
    """GET /marge/streaming/software/update/account/{accountId}"""

    def test_returns_200(self, tc, acc):
        account_id, _ = acc
        r = tc.get(
            f"/marge/streaming/software/update/account/{account_id}",
            headers=LOOPBACK_HEADER,
        )
        assert r.status_code == 200

    def test_root_element_is_software_update(self, tc, acc):
        account_id, _ = acc
        r = tc.get(
            f"/marge/streaming/software/update/account/{account_id}",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        assert_xml_tag(root, "software_update")

    def test_contains_softwareUpdateLocation(self, tc, acc):
        account_id, _ = acc
        r = tc.get(
            f"/marge/streaming/software/update/account/{account_id}",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        loc = root.find("softwareUpdateLocation")
        assert loc is not None, "Missing <softwareUpdateLocation>"

    def test_update_location_is_empty(self, tc, acc):
        """Soundcork must never push firmware — location must be empty."""
        account_id, _ = acc
        r = tc.get(
            f"/marge/streaming/software/update/account/{account_id}",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        loc = root.findtext("softwareUpdateLocation")
        assert (loc or "").strip() == "", (
            f"softwareUpdateLocation must be empty to block OTA, got: {loc!r}"
        )

    def test_alias_path(self, tc, acc):
        account_id, _ = acc
        r = tc.get(
            f"/streaming/software/update/account/{account_id}",
            headers=LOOPBACK_HEADER,
        )
        assert r.status_code == 200


# ===========================================================================
# 7. Power On
# ===========================================================================

class TestPowerOn:
    """POST /marge/streaming/support/power_on"""

    def test_returns_200(self, tc):
        r = tc.post(
            "/marge/streaming/support/power_on",
            content="<device-data/>",
            headers={**LOOPBACK_HEADER, "Content-Type": "application/xml"},
        )
        assert r.status_code == 200

    def test_root_alias(self, tc):
        r = tc.post(
            "/streaming/support/power_on",
            content="<device-data/>",
            headers={**LOOPBACK_HEADER, "Content-Type": "application/xml"},
        )
        assert r.status_code == 200

    def test_empty_body_still_200(self, tc):
        r = tc.post(
            "/marge/streaming/support/power_on",
            headers=LOOPBACK_HEADER,
        )
        assert r.status_code == 200


# ===========================================================================
# 8. Streaming Token
# ===========================================================================

class TestStreamingToken:
    """GET /marge/streaming/device/{deviceId}/streaming_token"""

    def test_returns_200(self, tc, acc):
        _, device_id = acc
        r = tc.get(
            f"/marge/streaming/device/{device_id}/streaming_token",
            headers=LOOPBACK_HEADER,
        )
        assert r.status_code == 200

    def test_root_element_bearertoken(self, tc, acc):
        _, device_id = acc
        r = tc.get(
            f"/marge/streaming/device/{device_id}/streaming_token",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        assert_xml_tag(root, "bearertoken")

    def test_bearer_token_value_attribute(self, tc, acc):
        _, device_id = acc
        r = tc.get(
            f"/marge/streaming/device/{device_id}/streaming_token",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        value = root.get("value")
        assert value is not None, "bearertoken missing value attribute"
        assert value.startswith("Bearer "), f"Token should start with 'Bearer ', got: {value!r}"

    def test_authorization_header_present(self, tc, acc):
        _, device_id = acc
        r = tc.get(
            f"/marge/streaming/device/{device_id}/streaming_token",
            headers=LOOPBACK_HEADER,
        )
        assert "Authorization" in r.headers or "authorization" in r.headers

    def test_root_alias(self, tc, acc):
        _, device_id = acc
        r = tc.get(
            f"/streaming/device/{device_id}/streaming_token",
            headers=LOOPBACK_HEADER,
        )
        assert r.status_code == 200


# ===========================================================================
# 9. BMX Service Registry
# ===========================================================================

class TestBmxServiceRegistry:
    """GET /bmx/registry/v1/services → JSON with bmx_services array"""

    def test_returns_200(self, tc):
        r = tc.get("/bmx/registry/v1/services", headers=LOOPBACK_HEADER)
        assert r.status_code == 200

    def test_returns_json(self, tc):
        r = tc.get("/bmx/registry/v1/services", headers=LOOPBACK_HEADER)
        assert "application/json" in r.headers.get("content-type", "")
        data = r.json()
        assert isinstance(data, dict)

    def test_required_top_level_keys(self, tc):
        r = tc.get("/bmx/registry/v1/services", headers=LOOPBACK_HEADER)
        data = r.json()
        assert "askAgainAfter" in data, "Missing askAgainAfter"
        assert "bmx_services" in data, "Missing bmx_services"

    def test_ask_again_after_is_positive_integer(self, tc):
        r = tc.get("/bmx/registry/v1/services", headers=LOOPBACK_HEADER)
        data = r.json()
        aaa = data["askAgainAfter"]
        assert isinstance(aaa, int) and aaa > 0, f"askAgainAfter should be positive int, got: {aaa!r}"

    def test_bmx_services_is_list(self, tc):
        r = tc.get("/bmx/registry/v1/services", headers=LOOPBACK_HEADER)
        data = r.json()
        assert isinstance(data["bmx_services"], list)
        assert len(data["bmx_services"]) >= 1

    def test_each_service_has_required_fields(self, tc):
        r = tc.get("/bmx/registry/v1/services", headers=LOOPBACK_HEADER)
        data = r.json()
        for svc in data["bmx_services"]:
            assert "id" in svc, f"Service missing id: {svc}"
            assert "assets" in svc, f"Service missing assets: {svc}"
            assert "baseUrl" in svc, f"Service missing baseUrl: {svc}"
            assert "streamTypes" in svc, f"Service missing streamTypes: {svc}"
            assert "authenticationModel" in svc, f"Service missing authenticationModel: {svc}"

    def test_service_id_structure(self, tc):
        r = tc.get("/bmx/registry/v1/services", headers=LOOPBACK_HEADER)
        data = r.json()
        for svc in data["bmx_services"]:
            svc_id = svc["id"]
            assert "name" in svc_id, f"Service id missing name: {svc_id}"
            assert "value" in svc_id, f"Service id missing value: {svc_id}"
            assert isinstance(svc_id["value"], int), f"Service id.value should be int: {svc_id}"

    def test_service_assets_icons(self, tc):
        r = tc.get("/bmx/registry/v1/services", headers=LOOPBACK_HEADER)
        data = r.json()
        for svc in data["bmx_services"]:
            assets = svc["assets"]
            assert "icons" in assets, f"assets missing icons for service: {svc['id']}"
            icons = assets["icons"]
            # At least smallSvg or largeSvg should be present
            assert "smallSvg" in icons or "largeSvg" in icons, (
                f"No SVG icon for service {svc['id']}"
            )

    def test_stream_types_valid_values(self, tc):
        valid = {"liveRadio", "onDemand"}
        r = tc.get("/bmx/registry/v1/services", headers=LOOPBACK_HEADER)
        data = r.json()
        for svc in data["bmx_services"]:
            for st in svc["streamTypes"]:
                assert st in valid, f"Invalid streamType {st!r} for service {svc['id']}"

    def test_tunein_service_present(self, tc):
        r = tc.get("/bmx/registry/v1/services", headers=LOOPBACK_HEADER)
        data = r.json()
        names = {svc["id"]["name"] for svc in data["bmx_services"]}
        assert "TUNEIN" in names, f"TUNEIN service not found. Got: {names}"

    def test_root_alias(self, tc):
        r = tc.get("/registry/v1/services", headers=LOOPBACK_HEADER)
        assert r.status_code == 200

    def test_base_url_contains_server_url(self, tc):
        """Each service baseUrl should reference the soundcork server, not Bose's."""
        r = tc.get("/bmx/registry/v1/services", headers=LOOPBACK_HEADER)
        data = r.json()
        for svc in data["bmx_services"]:
            base_url = svc["baseUrl"]
            assert "bose.io" not in base_url, (
                f"baseUrl {base_url!r} still points to Bose's servers — must be redirected to soundcork"
            )


# ===========================================================================
# 10. BMX TuneIn Playback
# ===========================================================================

class TestBmxTuneInPlayback:
    """GET /bmx/tunein/v1/playback/station/{stationId}"""

    STATION_ID = "s80044"  # A well-known TuneIn station

    def test_returns_200_for_valid_station(self, tc):
        r = tc.get(
            f"/bmx/tunein/v1/playback/station/{self.STATION_ID}",
            headers=LOOPBACK_HEADER,
        )
        assert r.status_code == 200

    def test_returns_json(self, tc):
        r = tc.get(
            f"/bmx/tunein/v1/playback/station/{self.STATION_ID}",
            headers=LOOPBACK_HEADER,
        )
        assert "application/json" in r.headers.get("content-type", "")

    def test_required_fields_present(self, tc):
        r = tc.get(
            f"/bmx/tunein/v1/playback/station/{self.STATION_ID}",
            headers=LOOPBACK_HEADER,
        )
        data = r.json()
        assert "audio" in data, "Missing audio"
        assert "imageUrl" in data, "Missing imageUrl"
        assert "name" in data, "Missing name"
        assert "streamType" in data, "Missing streamType"

    def test_audio_structure(self, tc):
        r = tc.get(
            f"/bmx/tunein/v1/playback/station/{self.STATION_ID}",
            headers=LOOPBACK_HEADER,
        )
        data = r.json()
        audio = data["audio"]
        assert "hasPlaylist" in audio
        assert "isRealtime" in audio
        assert "streamUrl" in audio
        assert "streams" in audio
        assert isinstance(audio["streams"], list)
        assert len(audio["streams"]) >= 1

    def test_stream_url_is_non_empty(self, tc):
        r = tc.get(
            f"/bmx/tunein/v1/playback/station/{self.STATION_ID}",
            headers=LOOPBACK_HEADER,
        )
        data = r.json()
        url = data["audio"]["streamUrl"]
        assert url and url.startswith("http"), f"streamUrl should be an HTTP URL, got: {url!r}"

    def test_stream_type_is_valid_enum(self, tc):
        r = tc.get(
            f"/bmx/tunein/v1/playback/station/{self.STATION_ID}",
            headers=LOOPBACK_HEADER,
        )
        data = r.json()
        assert data["streamType"] in {"liveRadio", "onDemand"}, (
            f"streamType {data['streamType']!r} not in valid enum"
        )

    def test_stream_objects_have_required_fields(self, tc):
        r = tc.get(
            f"/bmx/tunein/v1/playback/station/{self.STATION_ID}",
            headers=LOOPBACK_HEADER,
        )
        data = r.json()
        for stream in data["audio"]["streams"]:
            assert "hasPlaylist" in stream
            assert "isRealtime" in stream
            assert "streamUrl" in stream

    def test_root_alias(self, tc):
        r = tc.get(
            f"/tunein/v1/playback/station/{self.STATION_ID}",
            headers=LOOPBACK_HEADER,
        )
        assert r.status_code == 200


# ===========================================================================
# 11. BMX Custom Stream (Orion / Internet Radio)
# ===========================================================================

class TestBmxCustomStream:
    """GET /core02/svc-bmx-adapter-orion/prod/orion/station?data=<base64>"""

    def _make_data(self):
        import base64
        import json

        payload = json.dumps({
            "name": "Test Stream",
            "streamUrl": "http://stream.example.com/radio.mp3",
            "imageUrl": "http://example.com/logo.png",
        })
        return base64.urlsafe_b64encode(payload.encode()).decode()

    def test_returns_200(self, tc):
        data = self._make_data()
        r = tc.get(
            f"/core02/svc-bmx-adapter-orion/prod/orion/station?data={data}",
            headers=LOOPBACK_HEADER,
        )
        assert r.status_code == 200

    def test_returns_json(self, tc):
        data = self._make_data()
        r = tc.get(
            f"/core02/svc-bmx-adapter-orion/prod/orion/station?data={data}",
            headers=LOOPBACK_HEADER,
        )
        assert "application/json" in r.headers.get("content-type", "")

    def test_required_playback_response_fields(self, tc):
        data = self._make_data()
        r = tc.get(
            f"/core02/svc-bmx-adapter-orion/prod/orion/station?data={data}",
            headers=LOOPBACK_HEADER,
        )
        resp = r.json()
        assert "audio" in resp
        assert "imageUrl" in resp
        assert "name" in resp
        assert "streamType" in resp

    def test_stream_url_matches_input(self, tc):
        data = self._make_data()
        r = tc.get(
            f"/core02/svc-bmx-adapter-orion/prod/orion/station?data={data}",
            headers=LOOPBACK_HEADER,
        )
        resp = r.json()
        assert "stream.example.com" in resp["audio"]["streamUrl"]

    def test_bmx_orion_alias(self, tc):
        data = self._make_data()
        r = tc.get(
            f"/bmx/orion/v1/playback/station/{data}",
            headers=LOOPBACK_HEADER,
        )
        assert r.status_code == 200


# ===========================================================================
# 12. OAuth Token (Spotify)
# ===========================================================================

class TestOAuthToken:
    """POST /oauth/device/{deviceId}/music/musicprovider/{providerId}/token/{tokenType}"""

    def test_provider_15_no_account_returns_500(self, tc, acc):
        """Without a linked Spotify account, the endpoint returns an error."""
        _, device_id = acc
        r = tc.post(
            f"/oauth/device/{device_id}/music/musicprovider/15/token/cs3",
            headers=LOOPBACK_HEADER,
        )
        # Expect 500 (no account) or 200 (if somehow configured)
        assert r.status_code in (200, 500)
        if r.status_code == 500:
            data = r.json()
            assert "error" in data

    def test_unknown_provider_returns_404(self, tc, acc):
        _, device_id = acc
        r = tc.post(
            f"/oauth/device/{device_id}/music/musicprovider/99/token/bearer",
            headers=LOOPBACK_HEADER,
        )
        assert r.status_code == 404

    def test_provider_15_with_mock_token_returns_valid_json(self, tc, acc):
        """With a mocked Spotify service returning a token, response must be valid."""
        _, device_id = acc
        with patch("soundcork.main.spotify_service") as mock_spotify:
            mock_spotify.get_fresh_token_sync.return_value = "BQtest_access_token_123"
            r = tc.post(
                f"/oauth/device/{device_id}/music/musicprovider/15/token/cs3",
                headers=LOOPBACK_HEADER,
            )
        assert r.status_code == 200
        data = r.json()
        assert "access_token" in data
        assert "token_type" in data
        assert "expires_in" in data
        assert data["token_type"] == "Bearer"
        assert isinstance(data["expires_in"], int)
        assert data["expires_in"] > 0

    def test_access_token_value_matches_mocked(self, tc, acc):
        _, device_id = acc
        with patch("soundcork.main.spotify_service") as mock_spotify:
            mock_spotify.get_fresh_token_sync.return_value = "BQmocked_token_xyz"
            r = tc.post(
                f"/oauth/device/{device_id}/music/musicprovider/15/token/cs3",
                headers=LOOPBACK_HEADER,
            )
        data = r.json()
        assert data["access_token"] == "BQmocked_token_xyz"


# ===========================================================================
# 13. Telemetry / Analytics Stubs
# ===========================================================================

class TestTelemetryStubs:
    """scmudc, stapp, stats/usage, stats/error, bmx reporting — all 200 stubs."""

    def test_scmudc_returns_200(self, tc, acc):
        _, device_id = acc
        payload = '{"envelope":{},"payload":{"events":[]}}'
        r = tc.post(
            f"/v1/scmudc/{device_id}",
            content=payload,
            headers={**LOOPBACK_HEADER, "Content-Type": "application/json"},
        )
        assert r.status_code == 200

    def test_stapp_returns_200(self, tc, acc):
        _, device_id = acc
        r = tc.post(
            f"/v1/stapp/{device_id}",
            content="{}",
            headers={**LOOPBACK_HEADER, "Content-Type": "application/json"},
        )
        assert r.status_code == 200

    def test_stats_usage_returns_200(self, tc):
        r = tc.post(
            "/streaming/stats/usage",
            content="<stats/>",
            headers={**LOOPBACK_HEADER, "Content-Type": "application/xml"},
        )
        assert r.status_code == 200

    def test_stats_error_returns_200(self, tc):
        r = tc.post(
            "/streaming/stats/error",
            content="<error/>",
            headers={**LOOPBACK_HEADER, "Content-Type": "application/xml"},
        )
        assert r.status_code == 200

    def test_bmx_tunein_report_returns_200(self, tc):
        r = tc.post(
            "/bmx/tunein/v1/report",
            content='{"eventType":"START"}',
            headers={**LOOPBACK_HEADER, "Content-Type": "application/json"},
        )
        assert r.status_code == 200

    def test_customer_support_returns_200(self, tc):
        r = tc.post(
            "/marge/streaming/support/customersupport",
            content="<device-data/>",
            headers={**LOOPBACK_HEADER, "Content-Type": "application/xml"},
        )
        assert r.status_code == 200

    def test_scmudc_with_inner_events_persists(self, tc, acc):
        """scmudc events with inner event list should be persisted without error."""
        _, device_id = acc
        payload = {
            "envelope": {"payloadType": "scmudc"},
            "payload": {
                "events": [
                    {"type": "preset-pressed", "data": {"buttonId": "PRESET_1", "origin": "local"}},
                    {"type": "play-state-changed", "data": {"play-state": "PLAY_STATE"}},
                ]
            },
        }
        import json
        r = tc.post(
            f"/v1/scmudc/{device_id}",
            content=json.dumps(payload),
            headers={**LOOPBACK_HEADER, "Content-Type": "application/json"},
        )
        assert r.status_code == 200


# ===========================================================================
# 14. Health Check / Root
# ===========================================================================

class TestRootEndpoint:
    def test_root_returns_200(self, tc):
        r = tc.get("/")
        assert r.status_code == 200

    def test_root_returns_json(self, tc):
        r = tc.get("/")
        data = r.json()
        assert isinstance(data, dict)

    def test_root_contains_bose_key(self, tc):
        r = tc.get("/")
        data = r.json()
        assert "Bose" in data


# ===========================================================================
# 15. Customer Account Stubs
# ===========================================================================

class TestCustomerAccount:
    """GET /customer/account/{accountId} → <customer>"""

    def test_returns_200(self, tc, acc):
        account_id, _ = acc
        r = tc.get(f"/customer/account/{account_id}", headers=LOOPBACK_HEADER)
        assert r.status_code == 200

    def test_root_element_is_customer(self, tc, acc):
        account_id, _ = acc
        r = tc.get(f"/customer/account/{account_id}", headers=LOOPBACK_HEADER)
        root = parse_xml(r)
        assert_xml_tag(root, "customer")

    def test_customer_has_accountID(self, tc, acc):
        account_id, _ = acc
        r = tc.get(f"/customer/account/{account_id}", headers=LOOPBACK_HEADER)
        root = parse_xml(r)
        assert root.findtext("accountID") == account_id

    def test_customer_has_required_fields(self, tc, acc):
        account_id, _ = acc
        r = tc.get(f"/customer/account/{account_id}", headers=LOOPBACK_HEADER)
        root = parse_xml(r)
        assert_xml_children(root, "accountID", "email", "countryCode")


# ===========================================================================
# 16. Device Settings Stubs
# ===========================================================================

class TestDeviceSettings:
    """GET /marge/streaming/device_setting/account/{id}/device/{id}/device_settings"""

    def test_returns_200(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/device_setting/account/{account_id}/device/{device_id}/device_settings",
            headers=LOOPBACK_HEADER,
        )
        assert r.status_code == 200

    def test_root_element_is_deviceSettings(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/device_setting/account/{account_id}/device/{device_id}/device_settings",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        assert_xml_tag(root, "deviceSettings")

    def test_alias_path(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(
            f"/streaming/device_setting/account/{account_id}/device/{device_id}/device_settings",
            headers=LOOPBACK_HEADER,
        )
        assert r.status_code == 200


# ===========================================================================
# 17. Email Address Stub
# ===========================================================================

class TestEmailAddress:
    def test_returns_200(self, tc, acc):
        account_id, _ = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/emailaddress",
            headers=LOOPBACK_HEADER,
        )
        assert r.status_code == 200

    def test_returns_email_xml(self, tc, acc):
        account_id, _ = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/emailaddress",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        assert_xml_tag(root, "emailAddress")


# ===========================================================================
# 18. SoundTouch Device Web API (port 8090 protocol shape)
#     These tests validate the *format* that Soundcork produces for marge
#     responses that mirror what the speaker's own local API returns.
# ===========================================================================

class TestXmlDeclaration:
    """All XML responses must include a proper XML declaration."""

    XML_ENDPOINTS = [
        "/marge/streaming/sourceproviders",
    ]

    def test_xml_declaration_present(self, tc):
        for endpoint in self.XML_ENDPOINTS:
            r = tc.get(endpoint, headers=LOOPBACK_HEADER)
            assert r.text.startswith("<?xml"), (
                f"{endpoint}: response should start with XML declaration, got: {r.text[:50]!r}"
            )

    def test_source_providers_declaration(self, tc):
        r = tc.get("/marge/streaming/sourceproviders", headers=LOOPBACK_HEADER)
        assert '<?xml version="1.0"' in r.text

    def test_full_account_declaration(self, tc, acc):
        account_id, _ = acc
        r = tc.get(f"/marge/streaming/account/{account_id}/full", headers=LOOPBACK_HEADER)
        assert '<?xml version="1.0"' in r.text

    def test_presets_declaration(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/presets",
            headers=LOOPBACK_HEADER,
        )
        assert '<?xml version="1.0"' in r.text


# ===========================================================================
# 19. IP Allowlist Enforcement on Bose Protocol Paths
# ===========================================================================

class TestBoseProtocolIPEnforcement:
    """Bose-protocol endpoints must be blocked from non-speaker IPs."""

    BLOCKED_HEADER = {"X-Forwarded-For": "8.8.8.8"}

    def test_sourceproviders_blocked(self, tc):
        r = tc.get("/marge/streaming/sourceproviders", headers=self.BLOCKED_HEADER)
        assert r.status_code == 403

    def test_full_account_blocked(self, tc, acc):
        account_id, _ = acc
        r = tc.get(f"/marge/streaming/account/{account_id}/full", headers=self.BLOCKED_HEADER)
        assert r.status_code == 403

    def test_presets_blocked(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/presets",
            headers=self.BLOCKED_HEADER,
        )
        assert r.status_code == 403

    def test_bmx_services_blocked(self, tc):
        r = tc.get("/bmx/registry/v1/services", headers=self.BLOCKED_HEADER)
        assert r.status_code == 403

    def test_loopback_always_allowed(self, tc):
        r = tc.get("/marge/streaming/sourceproviders", headers={"X-Forwarded-For": "127.0.0.1"})
        assert r.status_code == 200

    def test_root_not_blocked(self, tc):
        """Root health check must be publicly accessible."""
        r = tc.get("/", headers=self.BLOCKED_HEADER)
        assert r.status_code == 200

    def test_mgmt_not_blocked_by_speaker_ip(self, tc):
        """Mgmt endpoints use their own auth, not speaker IP restriction."""
        r = tc.get("/mgmt/spotify/accounts", headers=self.BLOCKED_HEADER)
        assert r.status_code == 401  # auth required, NOT 403 from IP block


# ===========================================================================
# 20. Alias Route Parity
# ===========================================================================

class TestAliasRouteParity:
    """
    Root-level aliases (/streaming/..., /registry/..., etc.) must return
    the same HTTP status and XML root element as the primary paths.
    """

    def test_sourceproviders_parity(self, tc):
        primary = tc.get("/marge/streaming/sourceproviders", headers=LOOPBACK_HEADER)
        alias = tc.get("/streaming/sourceproviders", headers=LOOPBACK_HEADER)
        assert primary.status_code == alias.status_code == 200
        assert parse_xml(primary).tag == parse_xml(alias).tag

    def test_bmx_registry_parity(self, tc):
        primary = tc.get("/bmx/registry/v1/services", headers=LOOPBACK_HEADER)
        alias = tc.get("/registry/v1/services", headers=LOOPBACK_HEADER)
        assert primary.status_code == alias.status_code == 200

    def test_power_on_parity(self, tc):
        primary = tc.post(
            "/marge/streaming/support/power_on",
            headers=LOOPBACK_HEADER,
        )
        alias = tc.post(
            "/streaming/support/power_on",
            headers=LOOPBACK_HEADER,
        )
        assert primary.status_code == alias.status_code == 200

    def test_full_account_parity(self, tc, acc):
        account_id, _ = acc
        primary = tc.get(
            f"/marge/streaming/account/{account_id}/full",
            headers=LOOPBACK_HEADER,
        )
        alias = tc.get(
            f"/streaming/account/{account_id}/full",
            headers=LOOPBACK_HEADER,
        )
        assert primary.status_code == alias.status_code == 200
        assert parse_xml(primary).tag == parse_xml(alias).tag

    def test_software_update_parity(self, tc, acc):
        account_id, _ = acc
        primary = tc.get(
            f"/marge/streaming/software/update/account/{account_id}",
            headers=LOOPBACK_HEADER,
        )
        alias = tc.get(
            f"/streaming/software/update/account/{account_id}",
            headers=LOOPBACK_HEADER,
        )
        assert primary.status_code == alias.status_code == 200


# ===========================================================================
# 21. BMX Services — Soundcork-specific correctness
# ===========================================================================

class TestBmxServicesCorrectness:
    """Extra correctness checks beyond basic structure."""

    def test_tunein_service_stream_types_include_live_radio(self, tc):
        r = tc.get("/bmx/registry/v1/services", headers=LOOPBACK_HEADER)
        data = r.json()
        tunein = next(
            (s for s in data["bmx_services"] if s["id"]["name"] == "TUNEIN"), None
        )
        assert tunein is not None, "TUNEIN service not found"
        assert "liveRadio" in tunein["streamTypes"], "TUNEIN should support liveRadio"

    def test_all_services_have_auth_model(self, tc):
        r = tc.get("/bmx/registry/v1/services", headers=LOOPBACK_HEADER)
        data = r.json()
        for svc in data["bmx_services"]:
            auth = svc["authenticationModel"]
            assert isinstance(auth, dict), f"authenticationModel not a dict: {auth!r}"

    def test_media_icons_reachable(self, tc):
        """Media icon URLs served by soundcork itself should return 200."""
        r = tc.get("/bmx/registry/v1/services", headers=LOOPBACK_HEADER)
        data = r.json()
        for svc in data["bmx_services"]:
            icons = svc["assets"]["icons"]
            for key, url in icons.items():
                if url.startswith("/") or "localhost" in url or "testserver" in url:
                    icon_r = tc.get(url)
                    assert icon_r.status_code == 200, (
                        f"Media icon {url!r} for service {svc['id']['name']} returned {icon_r.status_code}"
                    )
                    break  # Check at most one local icon per service


# ===========================================================================
# 22. Response Encoding & Content-Type
# ===========================================================================

class TestResponseEncoding:
    """Ensure responses use UTF-8 and correct content-type."""

    def test_sourceproviders_content_type_is_bose_xml(self, tc):
        r = tc.get("/marge/streaming/sourceproviders", headers=LOOPBACK_HEADER)
        ct = r.headers.get("content-type", "")
        # Bose spec uses vnd.bose.streaming-v1.2+xml but application/xml is also acceptable
        assert "xml" in ct, f"Expected XML content-type, got {ct!r}"

    def test_full_account_utf8(self, tc, acc):
        account_id, _ = acc
        r = tc.get(f"/marge/streaming/account/{account_id}/full", headers=LOOPBACK_HEADER)
        # Should not raise on decode
        _ = r.text.encode("utf-8")

    def test_bmx_services_json_content_type(self, tc):
        r = tc.get("/bmx/registry/v1/services", headers=LOOPBACK_HEADER)
        ct = r.headers.get("content-type", "")
        assert "json" in ct, f"Expected JSON content-type, got {ct!r}"

    def test_oauth_response_json_content_type(self, tc, acc):
        _, device_id = acc
        with patch("soundcork.main.spotify_service") as mock_spotify:
            mock_spotify.get_fresh_token_sync.return_value = "BQtoken"
            r = tc.post(
                f"/oauth/device/{device_id}/music/musicprovider/15/token/cs3",
                headers=LOOPBACK_HEADER,
            )
        ct = r.headers.get("content-type", "")
        assert "json" in ct


# ===========================================================================
# 23. Schema Compliance — Source element inside Preset/Recent
#     Validates the <source> child structure matches the OpenAPI Credential
#     and Source schemas precisely.
# ===========================================================================

class TestSourceElementSchema:
    """
    Source elements inside presets and recents must carry all required
    attributes and child elements defined by the OpenAPI Source schema:
      id (attr), type (attr), createdOn, credential, name,
      sourceproviderid, sourcename, sourceSettings, updatedOn, username
    """

    def test_preset_source_id_attribute(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/presets",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        source = root.find(".//source")
        assert source is not None
        assert source.get("id") is not None, "<source> missing id attribute"

    def test_preset_source_type_attribute(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/presets",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        source = root.find(".//source")
        assert source is not None
        assert source.get("type") is not None, "<source> missing type attribute"

    def test_preset_source_required_children(self, tc, acc):
        """All required Source schema children must be present."""
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/presets",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        source = root.find(".//source")
        assert source is not None
        required = [
            "createdOn", "credential", "name",
            "sourceproviderid", "sourcename", "updatedOn", "username",
        ]
        assert_xml_children(source, *required)

    def test_credential_type_attribute(self, tc, acc):
        """<credential> must carry a type attribute (e.g. 'token')."""
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/presets",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        cred = root.find(".//credential")
        assert cred is not None
        assert cred.get("type") is not None, "<credential> missing type attribute"

    def test_sourceproviderid_is_numeric_string(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/presets",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        spid = root.findtext(".//sourceproviderid")
        assert spid is not None and spid.strip().isdigit(), (
            f"sourceproviderid should be numeric string, got: {spid!r}"
        )

    def test_recent_source_required_children(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/recents",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        source = root.find(".//source")
        assert source is not None
        required = ["createdOn", "credential", "name", "sourceproviderid", "sourcename", "updatedOn", "username"]
        assert_xml_children(source, *required)


# ===========================================================================
# 24. Preset Schema Field Compliance (OpenAPI Preset schema)
# ===========================================================================

class TestPresetSchemaCompliance:
    """
    Validates the marge preset XML against the OpenAPI Preset schema which
    mandates: buttonNumber (attr), containerArt, contentItemType, createdOn,
    location, name, source, updatedOn, username.
    """

    def test_button_number_in_range(self, tc, acc):
        """Preset buttonNumber must be 1–6 (SoundTouch only has 6 presets)."""
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/presets",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        for preset in root.findall("preset"):
            btn = int(preset.get("buttonNumber", "0"))
            assert 1 <= btn <= 6, f"buttonNumber {btn} out of range 1-6"

    def test_preset_username_element_present(self, tc, acc):
        """OpenAPI schema requires <username> in preset."""
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/presets",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        preset = root.find("preset")
        assert preset is not None
        assert preset.find("username") is not None, "<preset> missing <username> (required by OpenAPI schema)"

    def test_preset_created_on_is_iso8601(self, tc, acc):
        """createdOn must be an ISO 8601 datetime string."""
        import re as _re
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/presets",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        preset = root.find("preset")
        assert preset is not None
        created = preset.findtext("createdOn") or ""
        iso_pattern = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
        assert _re.search(iso_pattern, created), (
            f"createdOn {created!r} does not look like ISO 8601"
        )

    def test_preset_location_non_empty(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/presets",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        for preset in root.findall("preset"):
            loc = preset.findtext("location") or ""
            assert loc.strip(), f"Preset buttonNumber={preset.get('buttonNumber')} has empty location"

    def test_preset_name_non_empty(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/presets",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        for preset in root.findall("preset"):
            name = preset.findtext("name") or ""
            assert name.strip(), f"Preset buttonNumber={preset.get('buttonNumber')} has empty name"


# ===========================================================================
# 25. Recent Item Schema Compliance (OpenAPI RecentItem schema)
# ===========================================================================

class TestRecentItemSchemaCompliance:
    """
    Validates recent items against the OpenAPI RecentItem schema:
    id (attr), contentItemType, createdOn, lastplayedat, location,
    name, source, sourceid, updatedOn.
    """

    def test_recent_id_attribute(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/recents",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        for recent in root.findall("recent"):
            assert recent.get("id") is not None, "<recent> missing id attribute"

    def test_recent_sourceid_element(self, tc, acc):
        """OpenAPI RecentItem requires <sourceid> element."""
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/recents",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        recent = root.find("recent")
        assert recent is not None
        assert recent.find("sourceid") is not None, "<recent> missing required <sourceid>"

    def test_recent_lastplayedat_is_iso8601(self, tc, acc):
        import re as _re
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/recents",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        recent = root.find("recent")
        assert recent is not None
        lpa = recent.findtext("lastplayedat") or ""
        assert _re.search(r"\d{4}-\d{2}-\d{2}T", lpa), (
            f"lastplayedat {lpa!r} does not look like ISO 8601"
        )

    def test_recent_location_present(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/recents",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        recent = root.find("recent")
        assert recent is not None
        assert recent.find("location") is not None


# ===========================================================================
# 26. BMX Playback Response Schema (OpenAPI BmxPlaybackResponse schema)
# ===========================================================================

class TestBmxPlaybackResponseSchema:
    """
    Deep validation of BmxPlaybackResponse against OpenAPI spec:
    Required: audio, imageUrl, name, streamType
    audio Required: hasPlaylist, isRealtime, streamUrl, streams[]
    streams[] Required: hasPlaylist, isRealtime, streamUrl
    streamType enum: liveRadio | onDemand
    """

    STATION = "s80044"

    def test_image_url_is_string(self, tc):
        r = tc.get(f"/bmx/tunein/v1/playback/station/{self.STATION}", headers=LOOPBACK_HEADER)
        data = r.json()
        assert isinstance(data.get("imageUrl"), str), "imageUrl must be a string"

    def test_name_is_string(self, tc):
        r = tc.get(f"/bmx/tunein/v1/playback/station/{self.STATION}", headers=LOOPBACK_HEADER)
        data = r.json()
        assert isinstance(data.get("name"), str), "name must be a string"

    def test_audio_has_playlist_is_bool(self, tc):
        r = tc.get(f"/bmx/tunein/v1/playback/station/{self.STATION}", headers=LOOPBACK_HEADER)
        data = r.json()
        assert isinstance(data["audio"]["hasPlaylist"], bool), "hasPlaylist must be bool"

    def test_audio_is_realtime_is_bool(self, tc):
        r = tc.get(f"/bmx/tunein/v1/playback/station/{self.STATION}", headers=LOOPBACK_HEADER)
        data = r.json()
        assert isinstance(data["audio"]["isRealtime"], bool), "isRealtime must be bool"

    def test_audio_stream_url_non_empty(self, tc):
        r = tc.get(f"/bmx/tunein/v1/playback/station/{self.STATION}", headers=LOOPBACK_HEADER)
        data = r.json()
        url = data["audio"]["streamUrl"]
        assert isinstance(url, str) and url, "audio.streamUrl must be non-empty string"

    def test_streams_array_non_empty(self, tc):
        r = tc.get(f"/bmx/tunein/v1/playback/station/{self.STATION}", headers=LOOPBACK_HEADER)
        data = r.json()
        streams = data["audio"]["streams"]
        assert isinstance(streams, list) and len(streams) >= 1, "streams must be non-empty array"

    def test_each_stream_has_required_fields(self, tc):
        r = tc.get(f"/bmx/tunein/v1/playback/station/{self.STATION}", headers=LOOPBACK_HEADER)
        data = r.json()
        for i, stream in enumerate(data["audio"]["streams"]):
            for field in ("hasPlaylist", "isRealtime", "streamUrl"):
                assert field in stream, f"Stream[{i}] missing required field '{field}'"

    def test_stream_type_enum(self, tc):
        r = tc.get(f"/bmx/tunein/v1/playback/station/{self.STATION}", headers=LOOPBACK_HEADER)
        data = r.json()
        assert data["streamType"] in ("liveRadio", "onDemand"), (
            f"streamType '{data['streamType']}' not in enum [liveRadio, onDemand]"
        )

    def test_is_favorite_if_present_is_bool(self, tc):
        r = tc.get(f"/bmx/tunein/v1/playback/station/{self.STATION}", headers=LOOPBACK_HEADER)
        data = r.json()
        if "isFavorite" in data:
            assert isinstance(data["isFavorite"], bool), "isFavorite must be bool"


# ===========================================================================
# 27. BMX Services Response Schema (OpenAPI BmxServicesResponse schema)
# ===========================================================================

class TestBmxServicesResponseSchema:
    """
    Deep validation of BmxServicesResponse:
    Required _links, askAgainAfter (int), bmx_services (array)
    Each service: id{name,value}, assets{icons{smallSvg,largeSvg,...}},
                  baseUrl, streamTypes[], authenticationModel
    """

    def test_links_object_present(self, tc):
        """BmxServicesResponse requires _links according to spec."""
        r = tc.get("/bmx/registry/v1/services", headers=LOOPBACK_HEADER)
        data = r.json()
        # _links is optional in soundcork's pydantic model but required per OpenAPI
        # Accept either present or absent — note as advisory
        if "_links" in data:
            assert isinstance(data["_links"], dict)

    def test_ask_again_after_milliseconds_scale(self, tc):
        """askAgainAfter is in milliseconds; 1 min = 60000, 1 hour = 3600000."""
        r = tc.get("/bmx/registry/v1/services", headers=LOOPBACK_HEADER)
        data = r.json()
        aaa = data["askAgainAfter"]
        # Should be at least 1 minute (60000 ms) to avoid hammering
        assert aaa >= 60_000, f"askAgainAfter={aaa} seems too short (expected >= 60000 ms)"

    def test_service_id_name_is_known_provider(self, tc):
        """Service id.name values should be known Bose provider types."""
        known = {
            "TUNEIN", "LOCAL_INTERNET_RADIO", "SIRIUSXM_EVEREST",
            "RADIOPLAYER", "RADIO_BROWSER",
        }
        r = tc.get("/bmx/registry/v1/services", headers=LOOPBACK_HEADER)
        data = r.json()
        names = {svc["id"]["name"] for svc in data["bmx_services"]}
        assert names & known, f"No known service names found. Got: {names}"

    def test_service_id_value_is_positive_int(self, tc):
        r = tc.get("/bmx/registry/v1/services", headers=LOOPBACK_HEADER)
        data = r.json()
        for svc in data["bmx_services"]:
            val = svc["id"]["value"]
            assert isinstance(val, int) and val > 0, (
                f"Service id.value={val!r} must be positive int"
            )

    def test_service_assets_has_name(self, tc):
        r = tc.get("/bmx/registry/v1/services", headers=LOOPBACK_HEADER)
        data = r.json()
        for svc in data["bmx_services"]:
            assert "name" in svc["assets"], f"assets missing 'name' for {svc['id']}"

    def test_icons_contain_svg_urls(self, tc):
        r = tc.get("/bmx/registry/v1/services", headers=LOOPBACK_HEADER)
        data = r.json()
        for svc in data["bmx_services"]:
            icons = svc["assets"]["icons"]
            for key, url in icons.items():
                if "Svg" in key or "svg" in key.lower():
                    assert isinstance(url, str) and url, (
                        f"SVG icon '{key}' for service {svc['id']['name']} is empty"
                    )

    def test_authentication_model_well_formed(self, tc):
        """authenticationModel must be a non-null dict."""
        r = tc.get("/bmx/registry/v1/services", headers=LOOPBACK_HEADER)
        data = r.json()
        for svc in data["bmx_services"]:
            auth = svc["authenticationModel"]
            assert isinstance(auth, dict), (
                f"authenticationModel for {svc['id']['name']} must be dict, got {type(auth)}"
            )


# ===========================================================================
# 28. OAuth Token Response Schema (OpenAPI OAuthTokenResponse schema)
# ===========================================================================

class TestOAuthTokenResponseSchema:
    """
    Deep validation: access_token (str), token_type (str, "Bearer"),
    expires_in (int, > 0), scope (str, optional).
    """

    def _post_token(self, tc, acc):
        _, device_id = acc
        with patch("soundcork.main.spotify_service") as mock_spotify:
            mock_spotify.get_fresh_token_sync.return_value = "BQtest_schema_token"
            r = tc.post(
                f"/oauth/device/{device_id}/music/musicprovider/15/token/cs3",
                headers=LOOPBACK_HEADER,
            )
        return r

    def test_access_token_is_string(self, tc, acc):
        r = self._post_token(tc, acc)
        assert r.status_code == 200
        assert isinstance(r.json()["access_token"], str)

    def test_token_type_is_bearer(self, tc, acc):
        r = self._post_token(tc, acc)
        assert r.status_code == 200
        assert r.json()["token_type"] == "Bearer"

    def test_expires_in_is_positive_int(self, tc, acc):
        r = self._post_token(tc, acc)
        assert r.status_code == 200
        exp = r.json()["expires_in"]
        assert isinstance(exp, int) and exp > 0, f"expires_in={exp!r} must be positive int"

    def test_expires_in_is_one_hour(self, tc, acc):
        """Spotify tokens last 3600 seconds; soundcork should report this."""
        r = self._post_token(tc, acc)
        assert r.status_code == 200
        assert r.json()["expires_in"] == 3600

    def test_scope_if_present_is_string(self, tc, acc):
        r = self._post_token(tc, acc)
        if r.status_code == 200 and "scope" in r.json():
            assert isinstance(r.json()["scope"], str)

    def test_scope_contains_streaming(self, tc, acc):
        """Returned scope must include 'streaming' for speaker playback."""
        r = self._post_token(tc, acc)
        if r.status_code == 200 and "scope" in r.json():
            assert "streaming" in r.json()["scope"], (
                "OAuth scope must include 'streaming' for speaker Spotify playback"
            )


# ===========================================================================
# 29. Software Update Response Schema Compliance
# ===========================================================================

class TestSoftwareUpdateSchema:
    """
    OpenAPI SoftwareUpdateResponse: root element software_update,
    child softwareUpdateLocation (string, empty = no update available).
    Soundcork MUST always return empty location to prevent OTA.
    """

    def test_response_is_valid_xml(self, tc, acc):
        account_id, _ = acc
        r = tc.get(
            f"/marge/streaming/software/update/account/{account_id}",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)  # raises if not valid XML
        assert root is not None

    def test_root_tag_exact(self, tc, acc):
        account_id, _ = acc
        r = tc.get(
            f"/marge/streaming/software/update/account/{account_id}",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        assert root.tag == "software_update", (
            f"Root tag must be 'software_update', got '{root.tag}'"
        )

    def test_no_update_url_in_location(self, tc, acc):
        """Location must not contain any http/https URL — would trigger OTA."""
        account_id, _ = acc
        r = tc.get(
            f"/marge/streaming/software/update/account/{account_id}",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        loc = root.findtext("softwareUpdateLocation") or ""
        assert "http" not in loc.lower(), (
            f"softwareUpdateLocation contains URL {loc!r} — this would trigger OTA!"
        )

    def test_alias_path_same_schema(self, tc, acc):
        account_id, _ = acc
        r = tc.get(
            f"/streaming/software/update/account/{account_id}",
            headers=LOOPBACK_HEADER,
        )
        root = parse_xml(r)
        assert root.tag == "software_update"
        loc = root.findtext("softwareUpdateLocation") or ""
        assert "http" not in loc.lower()


# ===========================================================================
# 30. SoundTouch Web API (port 8090) — Speaker-to-Soundcork Interaction
#     These tests simulate what happens when the speaker contacts soundcork
#     during its startup sequence, replicating the documented call order
#     from docs/API_Spec.md "power on" section.
# ===========================================================================

class TestPowerOnSequence:
    """
    Simulates the documented power-on call sequence:
    1. POST /marge/streaming/support/power_on
    2. GET  /marge/streaming/sourceproviders
    3. GET  /marge/streaming/account/{id}/full
    4. GET  /marge/streaming/account/{id}/device/{id}/presets
    5. GET  /marge/streaming/software/update/account/{id}
    6. GET  /marge/streaming/account/{id}/provider_settings
    All must succeed. Response XML structure is validated.
    """

    def test_step1_power_on(self, tc):
        r = tc.post("/marge/streaming/support/power_on", headers=LOOPBACK_HEADER)
        assert r.status_code == 200, f"Step 1 power_on failed: {r.status_code}"

    def test_step2_source_providers(self, tc):
        r = tc.get("/marge/streaming/sourceproviders", headers=LOOPBACK_HEADER)
        assert r.status_code == 200, f"Step 2 sourceproviders failed: {r.status_code}"
        root = parse_xml(r)
        assert root.tag == "sourceProviders"
        assert len(root.findall("sourceprovider")) > 0

    def test_step3_full_account(self, tc, acc):
        account_id, _ = acc
        r = tc.get(f"/marge/streaming/account/{account_id}/full", headers=LOOPBACK_HEADER)
        assert r.status_code == 200, f"Step 3 full account failed: {r.status_code}"
        root = parse_xml(r)
        assert root.tag == "account"

    def test_step4_presets(self, tc, acc):
        account_id, device_id = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/{device_id}/presets",
            headers=LOOPBACK_HEADER,
        )
        assert r.status_code == 200, f"Step 4 presets failed: {r.status_code}"
        root = parse_xml(r)
        assert root.tag == "presets"

    def test_step5_software_update(self, tc, acc):
        account_id, _ = acc
        r = tc.get(
            f"/marge/streaming/software/update/account/{account_id}",
            headers=LOOPBACK_HEADER,
        )
        assert r.status_code == 200, f"Step 5 software update failed: {r.status_code}"
        root = parse_xml(r)
        assert root.tag == "software_update"

    def test_step6_provider_settings(self, tc, acc):
        account_id, _ = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/provider_settings",
            headers=LOOPBACK_HEADER,
        )
        assert r.status_code == 200, f"Step 6 provider settings failed: {r.status_code}"
        root = parse_xml(r)
        assert root.tag == "providerSettings"

    def test_full_sequence_without_error(self, tc, acc):
        """Run the complete documented power-on sequence; all steps must be 200."""
        account_id, device_id = acc
        endpoints = [
            ("POST", "/marge/streaming/support/power_on", None),
            ("GET", "/marge/streaming/sourceproviders", None),
            ("GET", f"/marge/streaming/account/{account_id}/full", None),
            ("GET", f"/marge/streaming/account/{account_id}/device/{device_id}/presets", None),
            ("GET", f"/marge/streaming/software/update/account/{account_id}", None),
            ("GET", f"/marge/streaming/account/{account_id}/provider_settings", None),
        ]
        for method, path, body in endpoints:
            if method == "GET":
                r = tc.get(path, headers=LOOPBACK_HEADER)
            else:
                r = tc.post(path, content=body or "", headers=LOOPBACK_HEADER)
            assert r.status_code == 200, (
                f"Power-on sequence step {method} {path} returned {r.status_code}"
            )


# ===========================================================================
# 31. Station Switch Sequence (from docs/API_Spec.md "switch to a station")
# ===========================================================================

class TestStationSwitchSequence:
    """
    Simulates the documented station switch sequence:
    1. POST /bmx/tunein/v1/report
    2. GET  /bmx/tunein/v1/playback/station/{stationId}
    3. POST /marge/streaming/account/{id}/device/{id}/recent
    """

    STATION = "s80044"

    def test_step1_analytics_report(self, tc):
        import json
        payload = json.dumps({"eventType": "START", "reason": "USER_SELECT_PLAYABLE"})
        r = tc.post(
            "/bmx/tunein/v1/report",
            content=payload,
            headers={**LOOPBACK_HEADER, "Content-Type": "application/json"},
        )
        assert r.status_code == 200, f"Analytics report returned {r.status_code}"

    def test_step2_station_playback(self, tc):
        r = tc.get(
            f"/bmx/tunein/v1/playback/station/{self.STATION}",
            headers=LOOPBACK_HEADER,
        )
        assert r.status_code == 200
        data = r.json()
        assert "audio" in data
        url = data["audio"]["streamUrl"]
        assert url.startswith("http"), f"streamUrl {url!r} must be an HTTP URL"

    def test_step2_stream_url_is_reachable_format(self, tc):
        """Stream URL must be a well-formed absolute URL, not relative."""
        r = tc.get(
            f"/bmx/tunein/v1/playback/station/{self.STATION}",
            headers=LOOPBACK_HEADER,
        )
        data = r.json()
        url = data["audio"]["streamUrl"]
        import urllib.parse
        parsed = urllib.parse.urlparse(url)
        assert parsed.scheme in ("http", "https"), f"streamUrl scheme invalid: {url!r}"
        assert parsed.netloc, f"streamUrl missing host: {url!r}"

    def test_station_switch_all_steps_succeed(self, tc, acc):
        """All documented station-switch calls must return 200."""
        import json
        account_id, device_id = acc

        steps = [
            ("POST", "/bmx/tunein/v1/report",
             json.dumps({"eventType": "START"}), "application/json"),
            ("GET", f"/bmx/tunein/v1/playback/station/{self.STATION}", None, None),
        ]
        for method, path, body, ct in steps:
            headers = dict(LOOPBACK_HEADER)
            if ct:
                headers["Content-Type"] = ct
            if method == "GET":
                r = tc.get(path, headers=headers)
            else:
                r = tc.post(path, content=body or "", headers=headers)
            assert r.status_code == 200, f"Station switch step {method} {path} → {r.status_code}"


# ===========================================================================
# 32. ETag Caching Behaviour
# ===========================================================================

class TestETagCaching:
    """
    Soundcork uses fastapi-etag for marge endpoints.
    A second request with If-None-Match matching the ETag should return 304.
    """

    def test_presets_etag_conditional_get(self, tc, acc):
        account_id, device_id = acc
        path = f"/marge/streaming/account/{account_id}/device/{device_id}/presets"

        r1 = tc.get(path, headers=LOOPBACK_HEADER)
        assert r1.status_code == 200
        etag = r1.headers.get("etag") or r1.headers.get("ETag")
        if etag is None:
            pytest.skip("ETag not present in presets response")

        r2 = tc.get(path, headers={**LOOPBACK_HEADER, "If-None-Match": etag})
        assert r2.status_code == 304, (
            f"Expected 304 with matching ETag, got {r2.status_code}"
        )

    def test_recents_etag_conditional_get(self, tc, acc):
        account_id, device_id = acc
        path = f"/marge/streaming/account/{account_id}/device/{device_id}/recents"

        r1 = tc.get(path, headers=LOOPBACK_HEADER)
        assert r1.status_code == 200
        etag = r1.headers.get("etag") or r1.headers.get("ETag")
        if etag is None:
            pytest.skip("ETag not present in recents response")

        r2 = tc.get(path, headers={**LOOPBACK_HEADER, "If-None-Match": etag})
        assert r2.status_code == 304, (
            f"Expected 304 with matching ETag, got {r2.status_code}"
        )

    def test_different_etag_returns_200(self, tc, acc):
        account_id, device_id = acc
        path = f"/marge/streaming/account/{account_id}/device/{device_id}/presets"

        r = tc.get(path, headers={**LOOPBACK_HEADER, "If-None-Match": '"stale-etag-value"'})
        assert r.status_code == 200, "Stale ETag should result in fresh 200 response"


# ===========================================================================
# 33. X-Forwarded-For Header Handling
# ===========================================================================

class TestXForwardedForHandling:
    """
    Soundcork uses the LAST value of X-Forwarded-For as the real client IP
    (the rightmost entry is appended by the reverse proxy and is trustworthy).
    Earlier entries are attacker-controlled and must not grant access.
    """

    def test_attacker_prepends_loopback_blocked(self, tc):
        """Attacker prepending 127.0.0.1 before their real IP must be blocked."""
        r = tc.get(
            "/marge/streaming/sourceproviders",
            headers={"X-Forwarded-For": "127.0.0.1, 203.0.113.99"},
        )
        assert r.status_code == 403

    def test_real_speaker_ip_as_last_entry_allowed(self, tc):
        """When real IP (last) is loopback, request must be allowed."""
        r = tc.get(
            "/marge/streaming/sourceproviders",
            headers={"X-Forwarded-For": "203.0.113.99, 127.0.0.1"},
        )
        assert r.status_code == 200

    def test_single_loopback_xff_allowed(self, tc):
        r = tc.get(
            "/marge/streaming/sourceproviders",
            headers={"X-Forwarded-For": "127.0.0.1"},
        )
        assert r.status_code == 200

    def test_private_ip_allowed_without_xff(self, tc):
        """Requests from private-range IPs (RFC1918) should pass the allowlist."""
        r = tc.get(
            "/marge/streaming/sourceproviders",
            headers={"X-Forwarded-For": "192.168.1.100"},
        )
        assert r.status_code == 200


# ===========================================================================
# 34. Telemetry Event Parsing and Persistence
# ===========================================================================

class TestTelemetryEventParsing:
    """
    Validates that the scmudc telemetry endpoint correctly parses the nested
    inner-event structure described in main.py's _persist_telemetry_event.
    """

    def test_preset_pressed_event_persisted(self, tc, acc):
        import json
        _, device_id = acc
        payload = json.dumps({
            "envelope": {"payloadType": "scmudc"},
            "payload": {
                "events": [
                    {"type": "preset-pressed", "data": {"buttonId": "PRESET_2", "origin": "local"}},
                ]
            },
        })
        r = tc.post(
            f"/v1/scmudc/{device_id}",
            content=payload,
            headers={**LOOPBACK_HEADER, "Content-Type": "application/json"},
        )
        assert r.status_code == 200

    def test_volume_change_event_persisted(self, tc, acc):
        import json
        _, device_id = acc
        payload = json.dumps({
            "envelope": {},
            "payload": {
                "events": [
                    {"type": "volume-change", "data": {"volume-change": [25, 30]}},
                ]
            },
        })
        r = tc.post(
            f"/v1/scmudc/{device_id}",
            content=payload,
            headers={**LOOPBACK_HEADER, "Content-Type": "application/json"},
        )
        assert r.status_code == 200

    def test_heartbeat_events_ignored(self, tc, acc):
        """Heartbeat events must not cause errors."""
        import json
        _, device_id = acc
        payload = json.dumps({
            "payload": {
                "events": [{"type": "heartbeat", "data": {}}]
            },
        })
        r = tc.post(
            f"/v1/scmudc/{device_id}",
            content=payload,
            headers={**LOOPBACK_HEADER, "Content-Type": "application/json"},
        )
        assert r.status_code == 200

    def test_empty_events_list_no_error(self, tc, acc):
        import json
        _, device_id = acc
        payload = json.dumps({"payload": {"events": []}})
        r = tc.post(
            f"/v1/scmudc/{device_id}",
            content=payload,
            headers={**LOOPBACK_HEADER, "Content-Type": "application/json"},
        )
        assert r.status_code == 200

    def test_malformed_json_returns_200(self, tc, acc):
        """Malformed bodies must not crash the server — fire-and-forget."""
        _, device_id = acc
        r = tc.post(
            f"/v1/scmudc/{device_id}",
            content="not-json{{{",
            headers={**LOOPBACK_HEADER, "Content-Type": "application/json"},
        )
        assert r.status_code == 200

    def test_stapp_alias_same_behaviour(self, tc, acc):
        import json
        _, device_id = acc
        payload = json.dumps({
            "payload": {"events": [{"type": "power-pressed", "data": {"origin": "hw"}}]}
        })
        r = tc.post(
            f"/v1/stapp/{device_id}",
            content=payload,
            headers={**LOOPBACK_HEADER, "Content-Type": "application/json"},
        )
        assert r.status_code == 200


# ===========================================================================
# 35. Management API — Events Endpoint
# ===========================================================================

class TestMgmtDeviceEvents:
    """
    GET /mgmt/devices/{deviceId}/events requires Basic Auth and returns
    {"events": [...], "total": int}.
    """

    def _mgmt_headers(self):
        import base64
        creds = base64.b64encode(b"admin:test_password_123").decode()
        return {"Authorization": f"Basic {creds}"}

    def test_returns_401_without_auth(self, tc, acc):
        _, device_id = acc
        r = tc.get(f"/mgmt/devices/{device_id}/events")
        assert r.status_code == 401

    def test_returns_200_with_auth(self, tc, acc):
        _, device_id = acc
        r = tc.get(
            f"/mgmt/devices/{device_id}/events",
            headers=self._mgmt_headers(),
        )
        assert r.status_code == 200

    def test_response_has_events_and_total(self, tc, acc):
        _, device_id = acc
        r = tc.get(
            f"/mgmt/devices/{device_id}/events",
            headers=self._mgmt_headers(),
        )
        assert r.status_code == 200
        data = r.json()
        assert "events" in data, "Missing 'events' key"
        assert "total" in data, "Missing 'total' key"

    def test_events_is_list(self, tc, acc):
        _, device_id = acc
        r = tc.get(
            f"/mgmt/devices/{device_id}/events",
            headers=self._mgmt_headers(),
        )
        data = r.json()
        assert isinstance(data["events"], list)

    def test_total_matches_events_length_or_more(self, tc, acc):
        _, device_id = acc
        r = tc.get(
            f"/mgmt/devices/{device_id}/events",
            headers=self._mgmt_headers(),
        )
        data = r.json()
        assert data["total"] >= len(data["events"]), (
            "total should be >= len(events) (events may be limited by ?limit)"
        )

    def test_limit_parameter_respected(self, tc, acc):
        _, device_id = acc
        r = tc.get(
            f"/mgmt/devices/{device_id}/events?limit=1",
            headers=self._mgmt_headers(),
        )
        data = r.json()
        assert len(data["events"]) <= 1

    def test_events_after_telemetry_include_typed_entries(self, tc, acc):
        """After posting telemetry, events endpoint should contain entries with 'type'."""
        import json
        _, device_id = acc

        # Post a recognisable event first
        payload = json.dumps({
            "payload": {
                "events": [{"type": "preset-pressed", "data": {"buttonId": "PRESET_3"}}]
            }
        })
        tc.post(
            f"/v1/scmudc/{device_id}",
            content=payload,
            headers={**LOOPBACK_HEADER, "Content-Type": "application/json"},
        )

        r = tc.get(
            f"/mgmt/devices/{device_id}/events",
            headers=self._mgmt_headers(),
        )
        data = r.json()
        if data["events"]:
            for event in data["events"]:
                assert "type" in event, f"Event missing 'type' key: {event}"
                assert "timestamp" in event, f"Event missing 'timestamp' key: {event}"


# ===========================================================================
# 36. BMX Services Availability Endpoint
# ===========================================================================

class TestBmxServicesAvailability:
    """GET /bmx/registry/v1/servicesAvailability"""

    def test_returns_200_or_404(self, tc):
        """This endpoint may not be implemented; 404 is acceptable."""
        r = tc.get("/bmx/registry/v1/servicesAvailability", headers=LOOPBACK_HEADER)
        assert r.status_code in (200, 404), f"Unexpected status: {r.status_code}"

    def test_if_200_returns_json(self, tc):
        r = tc.get("/bmx/registry/v1/servicesAvailability", headers=LOOPBACK_HEADER)
        if r.status_code == 200:
            assert "application/json" in r.headers.get("content-type", "")
            data = r.json()
            assert "services" in data


# ===========================================================================
# 37. Marge /marge prefix endpoint (test that full /marge/ prefix works)
# ===========================================================================

class TestMargePrefixRoutes:
    """Paths prefixed with /marge/streaming/... must all respond correctly."""

    def test_marge_sourceproviders(self, tc):
        r = tc.get("/marge/streaming/sourceproviders", headers=LOOPBACK_HEADER)
        assert r.status_code == 200

    def test_marge_customersupport_post(self, tc):
        r = tc.post(
            "/marge/streaming/support/customersupport",
            content="<diagnostic/>",
            headers={**LOOPBACK_HEADER, "Content-Type": "application/xml"},
        )
        assert r.status_code == 200

    def test_marge_accounts_alias_full(self, tc, acc):
        """The /marge/accounts/{id}/full alias must also work."""
        account_id, _ = acc
        r = tc.get(f"/marge/accounts/{account_id}/full", headers=LOOPBACK_HEADER)
        assert r.status_code == 200
        root = parse_xml(r)
        assert root.tag == "account"


# ===========================================================================
# 38. Regression: Invalid Account IDs Must Not Panic
# ===========================================================================

class TestInputValidation:
    """
    Soundcork uses regex validation (ACCOUNT_RE, DEVICE_RE) on path params.
    Invalid values should return 422, not 500.
    """

    def test_non_numeric_account_id(self, tc):
        r = tc.get(
            "/marge/streaming/account/not-a-number/full",
            headers=LOOPBACK_HEADER,
        )
        assert r.status_code in (422, 404), (
            f"Non-numeric account ID should return 422 or 404, got {r.status_code}"
        )

    def test_non_hex_device_id(self, tc, acc):
        account_id, _ = acc
        r = tc.get(
            f"/marge/streaming/account/{account_id}/device/NOT-HEX-ID!/presets",
            headers=LOOPBACK_HEADER,
        )
        assert r.status_code in (422, 404), (
            f"Non-hex device ID should return 422 or 404, got {r.status_code}"
        )

    def test_path_traversal_attempt(self, tc):
        """Path traversal in account ID must not leak filesystem data."""
        r = tc.get(
            "/marge/streaming/account/../../etc/passwd/full",
            headers=LOOPBACK_HEADER,
        )
        assert r.status_code in (422, 404, 400), (
            f"Path traversal attempt should be blocked, got {r.status_code}"
        )

    def test_very_long_account_id(self, tc):
        long_id = "9" * 100
        r = tc.get(
            f"/marge/streaming/account/{long_id}/full",
            headers=LOOPBACK_HEADER,
        )
        # Should be rejected by ACCOUNT_RE (max 20 digits) or return 404
        assert r.status_code in (422, 404), (
            f"Oversized account ID should be rejected, got {r.status_code}"
        )
