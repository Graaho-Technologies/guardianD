from __future__ import annotations

import time
from typing import Optional

import requests

from ..utils.logger import get_logger
from .base import BaseCollector, MetricSnapshot

_log = get_logger(__name__)

_IMDS_BASE = "http://169.254.169.254"


class EC2Collector(BaseCollector):
    name = "ec2"

    def __init__(self, timeout: int = 2, spot_check: bool = True) -> None:
        self.timeout = timeout
        self.spot_check = spot_check
        self._token: Optional[str] = None
        self._token_expiry: float = 0.0

    def _get_token(self) -> Optional[str]:
        now = time.time()
        if self._token and now < self._token_expiry:
            return self._token
        try:
            resp = requests.put(
                f"{_IMDS_BASE}/latest/api/token",
                headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                self._token = resp.text.strip()
                self._token_expiry = now + 21000
                return self._token
        except Exception:
            pass
        return None

    def _get(self, path: str, token: str) -> Optional[str]:
        try:
            resp = requests.get(
                f"{_IMDS_BASE}{path}",
                headers={"X-aws-ec2-metadata-token": token},
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                return resp.text.strip()
            if resp.status_code == 404:
                return None
        except Exception:
            pass
        return None

    def collect(self) -> MetricSnapshot:
        ts = time.time()
        try:
            token = self._get_token()
            if not token:
                return MetricSnapshot(
                    collector_name=self.name,
                    timestamp=ts,
                    metrics={
                        "is_ec2": False, "aws_account_id": "",
                        "instance_id": "", "instance_type": "",
                        "availability_zone": "", "region": "",
                        "ami_id": "", "public_ip": "", "private_ip": "",
                        "hostname": "", "iam_role": "", "instance_lifecycle": "",
                        "spot_interruption": {"scheduled": False, "action": "", "notice_time": ""},
                        "cpu_credits": {}, "ebs_volumes": [],
                    },
                    status="ok",
                )

            def g(path: str) -> str:
                return self._get(path, token) or ""

            region_raw = g("/latest/meta-data/placement/region")
            az = g("/latest/meta-data/placement/availability-zone")
            if not region_raw and az:
                region_raw = az[:-1] if az else ""

            # AWS account ID comes from the signed instance-identity document
            # (not exposed as a plain meta-data key). Region also lives here as
            # a fallback if the placement endpoints were unavailable.
            account_id = ""
            doc_raw = self._get("/latest/dynamic/instance-identity/document", token)
            if doc_raw:
                import json
                try:
                    doc = json.loads(doc_raw)
                    account_id = str(doc.get("accountId", "") or "")
                    if not region_raw:
                        region_raw = str(doc.get("region", "") or "")
                except Exception:
                    pass

            spot_info = {"scheduled": False, "action": "", "notice_time": ""}
            if self.spot_check:
                term_time = self._get("/latest/meta-data/spot/termination-time", token)
                action_raw = self._get("/latest/meta-data/spot/instance-action", token)
                if term_time:
                    spot_info["scheduled"] = True
                    spot_info["notice_time"] = term_time
                    spot_info["action"] = "terminate"
                if action_raw:
                    import json
                    try:
                        act = json.loads(action_raw)
                        spot_info["action"] = act.get("action", "terminate")
                        spot_info["notice_time"] = act.get("time", term_time or "")
                        spot_info["scheduled"] = True
                    except Exception:
                        pass

            iam_role = ""
            iam_creds_path = "/latest/meta-data/iam/security-credentials/"
            creds_list = self._get(iam_creds_path, token)
            if creds_list:
                iam_role = creds_list.strip().splitlines()[0] if creds_list else ""

            lifecycle = g("/latest/meta-data/instance-life-cycle") or "on-demand"

            ebs_volumes: list = []
            bdm = self._get("/latest/meta-data/block-device-mapping/", token)
            if bdm:
                for dev in bdm.strip().splitlines():
                    vol_id = self._get(f"/latest/meta-data/block-device-mapping/{dev.strip()}", token) or ""
                    ebs_volumes.append({"device": dev.strip(), "volume_id": vol_id})

            metrics = {
                "is_ec2": True,
                "aws_account_id": account_id,
                "instance_id": g("/latest/meta-data/instance-id"),
                "instance_type": g("/latest/meta-data/instance-type"),
                "availability_zone": az,
                "region": region_raw,
                "ami_id": g("/latest/meta-data/ami-id"),
                "public_ip": g("/latest/meta-data/public-ipv4"),
                "private_ip": g("/latest/meta-data/local-ipv4"),
                "hostname": g("/latest/meta-data/hostname"),
                "iam_role": iam_role,
                "instance_lifecycle": lifecycle,
                "cpu_credits": {},
                "spot_interruption": spot_info,
                "ebs_volumes": ebs_volumes,
            }
            return MetricSnapshot(collector_name=self.name, timestamp=ts, metrics=metrics)
        except Exception as exc:
            _log.error("ec2 collect error: %s", exc)
            return MetricSnapshot(
                collector_name=self.name, timestamp=ts,
                metrics={
                    "is_ec2": False, "aws_account_id": "",
                    "instance_id": "", "instance_type": "",
                    "availability_zone": "", "region": "", "ami_id": "",
                    "public_ip": "", "private_ip": "", "hostname": "",
                    "iam_role": "", "instance_lifecycle": "",
                    "spot_interruption": {"scheduled": False, "action": "", "notice_time": ""},
                    "cpu_credits": {}, "ebs_volumes": [],
                },
                status="error", error=str(exc),
            )
