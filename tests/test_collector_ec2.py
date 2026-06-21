from __future__ import annotations

from unittest.mock import MagicMock

from guardian.collector.ec2 import EC2Collector


def _mock_response(text: str, status: int = 200):
    r = MagicMock()
    r.status_code = status
    r.text = text
    return r


def test_ec2_not_on_ec2(mocker):
    mocker.patch("requests.put", side_effect=Exception("connection refused"))

    collector = EC2Collector(timeout=1)
    snap = collector.collect()

    assert snap.collector_name == "ec2"
    assert snap.metrics["is_ec2"] is False


def test_ec2_on_ec2(mocker):
    responses = {
        "/latest/api/token": _mock_response("fake-token"),
        "/latest/meta-data/instance-id": _mock_response("i-1234567890abcdef0"),
        "/latest/meta-data/instance-type": _mock_response("t3.medium"),
        "/latest/meta-data/placement/availability-zone": _mock_response("us-east-1a"),
        "/latest/meta-data/placement/region": _mock_response("us-east-1"),
        "/latest/meta-data/ami-id": _mock_response("ami-12345678"),
        "/latest/meta-data/public-ipv4": _mock_response("1.2.3.4"),
        "/latest/meta-data/local-ipv4": _mock_response("10.0.0.1"),
        "/latest/meta-data/hostname": _mock_response("ip-10-0-0-1.compute.internal"),
        "/latest/meta-data/iam/security-credentials/": _mock_response("", 404),
        "/latest/meta-data/spot/termination-time": _mock_response("", 404),
        "/latest/meta-data/spot/instance-action": _mock_response("", 404),
        "/latest/dynamic/instance-identity/document": _mock_response(
            '{"accountId": "123456789012", "region": "us-east-1", '
            '"instanceId": "i-1234567890abcdef0"}'
        ),
    }

    def mock_get(url, **kwargs):
        for path, resp in responses.items():
            if url.endswith(path):
                return resp
        r = MagicMock()
        r.status_code = 404
        r.text = ""
        return r

    mocker.patch("requests.put", return_value=_mock_response("fake-token"))
    mocker.patch("requests.get", side_effect=mock_get)

    collector = EC2Collector(timeout=2, spot_check=True)
    snap = collector.collect()

    assert snap.metrics["is_ec2"] is True
    assert snap.metrics["instance_id"] == "i-1234567890abcdef0"
    assert snap.metrics["instance_type"] == "t3.medium"
    assert snap.metrics["spot_interruption"]["scheduled"] is False
    # AWS account id is parsed from the signed instance-identity document.
    assert snap.metrics["aws_account_id"] == "123456789012"


def test_ec2_account_id_blank_when_identity_doc_missing(mocker):
    # No identity document available → account id falls back to empty string,
    # never raises, and the rest of the snapshot still populates.
    responses = {
        "/latest/api/token": _mock_response("fake-token"),
        "/latest/meta-data/instance-id": _mock_response("i-abc"),
        "/latest/meta-data/placement/availability-zone": _mock_response("us-east-1a"),
        "/latest/meta-data/placement/region": _mock_response("us-east-1"),
    }

    def mock_get(url, **kwargs):
        for path, resp in responses.items():
            if url.endswith(path):
                return resp
        r = MagicMock()
        r.status_code = 404
        r.text = ""
        return r

    mocker.patch("requests.put", return_value=_mock_response("fake-token"))
    mocker.patch("requests.get", side_effect=mock_get)

    snap = EC2Collector(timeout=2).collect()
    assert snap.metrics["is_ec2"] is True
    assert snap.metrics["aws_account_id"] == ""


def test_ec2_not_on_ec2_has_account_key(mocker):
    # Even off-EC2, the key must exist so downstream code/labels never KeyError.
    mocker.patch("requests.put", side_effect=Exception("connection refused"))
    snap = EC2Collector(timeout=1).collect()
    assert snap.metrics["aws_account_id"] == ""
