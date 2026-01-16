import pickle
import random
import traceback
import httpx
import time
import math
from helpers import env
from datetime import datetime
from helpers.aes_token import AESCipher, ConfidentialTokenService
from helpers.date_processing import date_matches, complete_months, validate_ranges

# setup

base_url = env.get_string("BASE_URL")
atc_validity_duration = env.get_int("ATC_VALIDITY_DURATION")
max_permits = env.get_int("MAX_PERMITS")
role_id = env.get_string("ROLE_ID")

ranges = env.get_json("DATE_RANGE")
validate_ranges(ranges)

api_ranges = complete_months(ranges)

endpoint = "https://www.recreation.gov/api/permitinyo/445860/availabilityv2"
heartbeat = "https://uptime.gdodge.dev/api/push/6J1vpSBPUk?status=up&msg=OK&ping="

hooks = {
    "event": env.get_string("EVENT_HOOK"),
    "logging": env.get_string("LOG_HOOK"),
}

headers = {
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "en-US,en;q=0.5",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Pragma": "no-cache",
    "Referer": "https://www.recreation.gov/permits/445860/registration/detailed-availability",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "TE": "trailers",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0"
}

# load data

try:
    previous_data = pickle.load(open("data/previous_data.p", "rb"))
except FileNotFoundError:
    previous_data = {}

# AES setup
aes_key = AESCipher.derive_key_from_secret(env.get_string("SECRET_KEY"), length=32)
AESTokenService = ConfidentialTokenService()
AESTokenService.add_key(0, aes_key)


def append_change(changelist, permit_date, permit_type, permit_code, old_avail, new_avail):
    changelist.append({
        "date": permit_date,
        "timestamp": int(time.time()),
        "permit_type": permit_type,
        "permit_code": permit_code,
        "old_availability": old_avail,
        "new_availability": new_avail,
        "token": AESTokenService.issue_token({
            "date": permit_date,
            "permit": permit_code,
            "count": min(new_avail, max_permits)
        }, ttl_seconds=atc_validity_duration, kid=0),
    })


def log_event(content, silent=False):
    content = f"[Logging/{datetime.now().strftime('%m/%d/%y %H:%M')}] - {content}"
    print(content)
    if not silent:
        send_hook("logging", content=content)


def generate_embeds(changes):
    msgs = []

    msg_count = math.ceil(len(changes) / 10)
    for i in range(0, msg_count):
        msgs.append({
            "content": "",
            "tts": False,
            "embeds": [],
            "components": [],
            "actions": {},
            "flags": 0,
            "username": "Mt. Whitney Permit Availability Monitor",
            "avatar_url": "https://upload.wikimedia.org/wikipedia/commons/f/f9/Mount_Whitney_2003-03-25.jpg"
        })

    if role_id:
        msgs[0]["content"] = f"<@&{role_id}>"  # ping env role

    curr_msg_index = 0
    for change in changes:
        if len(msgs[curr_msg_index]["embeds"]) >= 10:
            curr_msg_index += 1

        msgs[curr_msg_index]["embeds"].append({
            "description": f"Update Timestamp: <t:{int(time.time())}:T>",
            "fields": [
                {
                    "name": "Date",
                    "value": change["date"],
                    "inline": True
                },
                {
                    "name": "Permit Type",
                    "value": change["permit_type"],
                    "inline": True
                },
                {
                    "name": "Availability Change",
                    "value": f"{change['old_availability']} -> {change['new_availability']}",
                    "inline": True
                },
                {
                    "name": f"Auto-Reserve (Expires <t:{int(time.time())+atc_validity_duration}:R>)",
                    "value": f"[Click Here]({base_url}/atc"
                             f"?token={change['token']})"
                },
                {
                    "name": "Regular ATC",
                    "value": "[Click Here]"
                             "(https://www.recreation.gov/permits/445860/registration/detailed-availability)"
                }
            ],
            "title": "Availability Update"
        })

    return msgs


def send_hook(channel, content=None, embeds=None, full_json=None):
    if full_json is None:
        httpx.post(hooks[channel], json={"content": content,
                                         "embeds": embeds})
    else:
        httpx.post(hooks[channel], json=full_json)


if __name__ == "__main__":
    log_event(content="Monitor Online, in 15s startup cooldown.")
    time.sleep(15)
    while True:
        try:
            httpx.get(heartbeat)

            errors = []
            data = []
            for date in api_ranges:
                try:
                    req = httpx.get(endpoint, params={
                        "start_date": date[0],
                        "end_date": date[1],
                        "rid": random.randint(0, 9999999)  # cache bypass
                    }, headers=headers)

                    if 'error' in req.json():
                        errors.append(f"[step: post-request] error on {date[0]} -> {date[1]}."
                                      f" Info: {req.text} Status Code: {req.status_code}")
                    else:
                        data.append(req.json()["payload"])
                except Exception as e:
                    errors.append(
                        f"[step: request] error on {date[0]}-{date[1]}. Info: {traceback.format_exception(e)} ")

            flattened_data = {}
            for d in data:
                flattened_data.update(d)

            total_dates = len(flattened_data)

            # remove dates that are outside of ranges
            removed_dates = []
            for date_key in list(flattened_data.keys()):
                if not date_matches(date_key, ranges):
                    removed_dates.append(date_key)
                    del flattened_data[date_key]

            modified_keys = (
                    # keys added or removed
                    set(previous_data.keys()) ^ set(flattened_data.keys())
                    | {k for k in previous_data.keys() & flattened_data.keys() if previous_data[k] != flattened_data[k]}
                    # keys with different values
            )

            changes = []
            if len(modified_keys) > 0:
                data_changed = True
                logging_addendum = "Availability has changed since last update"
                for key in modified_keys:
                    previous_day_permits = 0
                    new_day_permits = 0
                    previous_overnight_permits = 0
                    new_overnight_permits = 0

                    if key in previous_data:
                        if '406' in previous_data[key]:
                            previous_day_permits = previous_data[key]['406']["quota_usage_by_member_daily"]["remaining"]
                        if '166' in previous_data[key]:
                            previous_overnight_permits = previous_data[key]['166']["quota_usage_by_member_daily"][
                                "remaining"]

                    if key in flattened_data:
                        if '406' in flattened_data[key]:
                            new_day_permits = flattened_data[key]['406']["quota_usage_by_member_daily"]["remaining"]
                        if '166' in flattened_data[key]:
                            new_overnight_permits = flattened_data[key]['166']["quota_usage_by_member_daily"][
                                "remaining"]

                    if previous_day_permits != new_day_permits:
                        append_change(changes,
                                      key,
                                      "Day Permit (JM34.5)",
                                      "JM34.5",
                                      previous_day_permits,
                                      new_day_permits)

                    if previous_overnight_permits != new_overnight_permits:
                        append_change(changes,
                                      key,
                                      "Overnight Permit (JM35)",
                                      "JM35",
                                      previous_overnight_permits,
                                      new_overnight_permits)
            else:
                data_changed = False
                logging_addendum = "Data is unchanged from last fetch"

            if changes:
                changes = sorted(changes, key=lambda x: datetime.strptime(x["date"], "%Y-%m-%d"), reverse=True)
                msgs = generate_embeds(changes)
                for msg in msgs:
                    send_hook("event", full_json=msg)

            if errors:
                errors_addendum = "\n".join(errors)
            else:
                errors_addendum = "No errors"

            if removed_dates:
                removed_addendum = "Removed Non-Matching Dates:\n"
                removed_addendum += "\n".join(removed_dates)
            else:
                removed_addendum = "No Removed Dates"

            silent = not (errors or data_changed)
            log_event(silent=silent, content=f"Got Availability:"
                                             f"\n{total_dates} Dates"
                                             f"\n{logging_addendum}"
                                             f"\n{errors_addendum}"
                                             f"\n{removed_addendum}")

            pickle.dump(flattened_data, open("data/previous_data.p", "wb"))
            previous_data = flattened_data
        except Exception as e:
            try:
                log_event(content=f"Unhandled Exception: {traceback.format_exception(e)}")
            except:
                print("Reached unhandled exception and could not POST msg.")
        time.sleep(30)  # cooldown
