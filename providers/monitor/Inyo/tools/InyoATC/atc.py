import json

import httpx
import datetime
import time
import redis
from .exceptions import SigninError
from lib.config import Config
from . import utils

MAX_V3_WAIT = 15  # seconds
V3_INTERVAL = 0.05

# pub sub channel format
CHANNEL_FMT = utils.CHANNEL_FMT


class Messanger:
    def __init__(self, cfg: Config, txid=None):
        self.txid = txid
        self.r = redis.Redis.from_url(cfg.monitor.providers['Inyo'].tools['InyoATC']['redis_url'])

    def send(self, data: dict):
        if self.txid:
            channel = CHANNEL_FMT.format(txid=self.txid)
            message = json.dumps(data)
            self.r.publish(channel, message)


class InyoAtc:
    def __init__(
            self,
            username: str,
            password: str,
            fingerprint: str,
            headers: dict,
            cfg: dict,
            prep_v3=False,
            txid=None
    ) -> None:
        self.headers = headers
        self.cfg = Config.from_dict(cfg)
        self.username = username
        self.password = password
        self.fingerprint = fingerprint
        self.client = httpx.Client(headers=self.headers)
        self.account = None
        self.RECAPTCHA_URL_BASE = (f"{self.cfg.service.base_url}"
                                   f"{self.cfg.monitor.providers['Inyo'].tools['Harvester']['url_prefix']}")

        self._v3_task_id = None
        self.messanger = Messanger(self.cfg, txid)
        self.messanger.send({
            "percent": 3,
            "stage": "Task Initialized"
        })

        if prep_v3:
            self.get_v3_task_id()  # start on the captcha solve early

    def get_v3_task_id(self, msg=True):
        if msg:
            self.messanger.send({
                "percent": 10,
                "stage": "Requesting Captcha"
            })
        r = self.client.request("GET",
                                f"{self.RECAPTCHA_URL_BASE}/start_token_task",
                                headers={
                                    "X-IPC-KEY": self.cfg.IPC_key
                                }
                                )
        self._v3_task_id = r.json()["task_id"]

    def get_v3_solution(self):
        if not self._v3_task_id:
            self.get_v3_task_id(msg=False)  # silently get v3 task

        waiting = True
        termination_time = datetime.datetime.now() + datetime.timedelta(seconds=MAX_V3_WAIT)

        while waiting:
            self.messanger.send({
                "percent": 80,
                "stage": "Waiting for Captcha Solution"
            })
            r = self.client.request("GET",
                                    f"{self.RECAPTCHA_URL_BASE}/task_status/{self._v3_task_id}",
                                    headers={
                                        "X-IPC-KEY": self.cfg.IPC_key
                                    }
                                    )
            if r.json()["state"] not in ["PENDING", "SUCCESS"]:
                raise Exception("Could not solve v3 captcha - Error")

            if r.json()["state"] == "SUCCESS":
                return r.json()["result"]

            if termination_time < datetime.datetime.now():
                raise Exception("Could not solve v3 captcha - Timeout")
            time.sleep(V3_INTERVAL)

    def login(self):
        self.messanger.send({
            "percent": 20,
            "stage": "Signing In"
        })
        try:
            r = self.client.request("POST", "https://www.recreation.gov/api/accounts/login/v2/", json={
                "username": self.username,
                "password": self.password,
                "fingerprint": self.fingerprint,
                "userAgent": self.headers["User-Agent"]
            })
        except Exception as e:
            self.messanger.send({
                "percent": 0,
                "stage": "Signin Error. Check your account credentials."
            })
            raise SigninError("Could not sign in to account") from e

        if (r.status_code == 200) and (r.json()["access_token"]):
            self.account = r.json()
        else:
            self.messanger.send({
                "percent": 0,
                "stage": "Signin Error. Check your account credentials."
            })
            raise SigninError(f"Message: {r.text}. Status: {r.status_code}")

    def add_to_cart(self, permit_id, target_id, start_date, end_date, group_count):
        captcha_code = self.get_v3_solution()

        self.messanger.send({
            "percent": 95,
            "stage": "Adding to cart"
        })

        body = {"entrance": permit_id,
                "permit_id": target_id,
                "division_id": permit_id,
                "user_id": self.account["account"]["account_id"],
                "start_date": start_date,
                "status": 0,
                "group_members": [{"first_name": "", "middle_name": "", "last_name": "", "remark": "",
                                   "group_member_type": "Adult"} for _ in range(group_count)],
                "extra_fields":
                    {"quota_type": "advanced", "category": "non-commercial"},
                "permit_type": "",
                "end_date": end_date,
                "system": {"code": captcha_code,
                           "region": "ENTERPRISE",
                           "section": "LBEAvailabilityPage"},
                "permit_holder":
                    {"email": self.account["account"]["email"],
                     "first_name": self.account["account"]["first_name"],
                     "last_name": self.account["account"]["last_name"],
                     "account_id": self.account["account"]["account_id"],
                     "home_address": self.account["account"]["home_address"],
                     "home_phone": self.account["account"]["home_phone"],
                     "cell_phone": self.account["account"]["cell_phone"]
                     },
                "sales_channel_type": 0}

        headers = self.headers
        headers["Authorization"] = f"Bearer {self.account['access_token']}"

        r = self.client.request("POST",
                                "https://www.recreation.gov/api/permitinyo/issuancesv2",
                                json=body,
                                headers=headers)

        print(r.text)

        if r.status_code == 201:
            # lookup for "friendly" permit name
            friendlyName = self.cfg.monitor.providers['Inyo'].raw['permit_codes'][permit_id]
            itemName = f"{friendlyName} {start_date}"

            self.messanger.send({
                "percent": 100,
                "stage": "Success - Visit Recreation.gov to finish checking out.",
                "done": True,
                "itemName": itemName
            })
        else:
            self.messanger.send({
                "percent": 0,
                "stage": f"Failed - {r.text}"
            })
