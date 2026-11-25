"""Pair two dev accounts as today's daily buddies.

The chat service on the dev API host does the insert, the same way the
noon job does. The database password stays on that host.
"""

from __future__ import annotations

import re
import time

import boto3
from botocore.exceptions import BotoCoreError, ClientError, ProfileNotFound

from sticker_pack import DEV_PROFILE, DEV_REGION, PackError

ASG_NAME = "localcircle-api"
_INDIAN = re.compile(r"^(?:\+91|91|0)?([6-9]\d{9})$")
_PHONE = re.compile(r"^\+91[6-9]\d{9}$")


class BuddyError(PackError):
    """These two numbers cannot be paired."""


def check_phone(raw: str) -> str:
    compact = re.sub(r"[\s\-()]", "", (raw or "").strip())
    match = _INDIAN.match(compact)
    if not match:
        raise BuddyError("Enter a 10-digit Indian mobile number.")
    return "+91" + match.group(1)


def pair_daily_buddies(left_raw: str, right_raw: str, *, profile: str = DEV_PROFILE) -> dict:
    left = check_phone(left_raw)
    right = check_phone(right_raw)
    if left == right:
        raise BuddyError("Enter two different numbers.")
    output = _run_on_dev(_host_script(left, right), profile)
    return _outcome(output, left, right)


def parse_buddy_output(text: str) -> dict:
    """Read the LC_BUDDY line the dev chat service prints."""
    line = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("LC_BUDDY "):
            line = stripped
    if not line:
        raise BuddyError("The dev server did not confirm the pair.")
    parts = line.split(" ")
    code = parts[1] if len(parts) > 1 else ""
    if code == "PAIRED" and len(parts) >= 4:
        return {"code": "PAIRED", "day": parts[2], "freed": parts[3] == "1"}
    if code == "ALREADY" and len(parts) >= 3:
        return {"code": "ALREADY", "day": parts[2], "freed": False}
    if code == "MISSING" and len(parts) >= 3:
        return {"code": "MISSING", "phone": parts[2]}
    if code == "BUSY" and len(parts) >= 4:
        return {"code": "BUSY", "phone": parts[2], "other": parts[3]}
    if code == "NOCHAT":
        return {"code": "NOCHAT"}
    if code == "ERROR":
        return {"code": "ERROR", "detail": line.split(" ", 2)[2] if line.count(" ") >= 2 else "Pairing failed."}
    raise BuddyError("The dev server did not confirm the pair.")


def _outcome(text: str, left: str, right: str) -> dict:
    parsed = parse_buddy_output(text)
    code = parsed["code"]
    if code == "PAIRED":
        message = f"{left} and {right} are daily buddies for {parsed['day']}."
        if parsed["freed"]:
            message += " A closed buddy from earlier today was cleared so this pair could be saved."
        return {"message": message, "day": parsed["day"]}
    if code == "ALREADY":
        return {"message": f"{left} and {right} are already daily buddies for {parsed['day']}.", "day": parsed["day"]}
    if code == "MISSING":
        raise BuddyError(f"No account on dev uses {parsed['phone']}.")
    if code == "BUSY":
        raise BuddyError(
            f"{parsed['phone']} already has {parsed['other']} as a daily buddy today. "
            "An open buddy is left as it is."
        )
    if code == "NOCHAT":
        raise BuddyError("The dev chat service is not running.")
    raise BuddyError(str(parsed.get("detail") or "Pairing failed."))


def _host_script(left: str, right: str) -> str:
    if not _PHONE.match(left) or not _PHONE.match(right):
        raise BuddyError("Enter a 10-digit Indian mobile number.")
    python = _REMOTE_PYTHON.replace("PHONE_LEFT", left).replace("PHONE_RIGHT", right)
    return _REMOTE_SHELL.replace("REMOTE_PYTHON", python)


def _run_on_dev(script: str, profile: str) -> str:
    try:
        session = boto3.Session(profile_name=(profile or DEV_PROFILE).strip() or DEV_PROFILE, region_name=DEV_REGION)
        ssm = session.client("ssm")
        autoscaling = session.client("autoscaling")
    except ProfileNotFound as exc:
        raise BuddyError(f"AWS profile {profile or DEV_PROFILE} is not on this computer.") from exc
    host = _api_host(autoscaling)
    try:
        command_id = ssm.send_command(
            InstanceIds=[host],
            DocumentName="AWS-RunShellScript",
            TimeoutSeconds=120,
            Comment="localcircle daily buddy",
            Parameters={"commands": [script]},
        )["Command"]["CommandId"]
    except (ClientError, BotoCoreError) as exc:
        raise BuddyError(f"Could not reach the dev server. {exc}") from exc
    deadline = time.time() + 90
    while time.time() < deadline:
        time.sleep(2)
        try:
            result = ssm.get_command_invocation(CommandId=command_id, InstanceId=host)
        except ClientError as exc:
            if _error_code(exc) == "InvocationDoesNotExist":
                continue
            raise BuddyError(f"Could not reach the dev server. {exc}") from exc
        status = result.get("Status") or ""
        if status in {"Pending", "InProgress", "Delayed"}:
            continue
        output = f"{result.get('StandardOutputContent') or ''}\n{result.get('StandardErrorContent') or ''}"
        try:
            parse_buddy_output(output)
        except BuddyError:
            if status != "Success":
                detail = _tail(output)
                raise BuddyError(detail or f"The dev server could not pair them ({status}).") from None
            raise
        return output
    raise BuddyError("The dev server took too long.")


def _api_host(autoscaling) -> str:
    try:
        described = autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=[ASG_NAME])
    except (ClientError, BotoCoreError) as exc:
        raise BuddyError(f"Could not reach the dev server. {exc}") from exc
    groups = described.get("AutoScalingGroups") or []
    if not groups:
        raise BuddyError("The dev API servers are not set up.")
    hosts = [
        item["InstanceId"]
        for item in groups[0].get("Instances") or []
        if item.get("LifecycleState") == "InService" and item.get("InstanceId")
    ]
    if not hosts:
        raise BuddyError("No dev API server is running.")
    return hosts[0]


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


def _tail(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    return lines[-1][:400]


_REMOTE_SHELL = """set -eu
cid=$(docker ps --format '{{.ID}} {{.Image}}' | awk '/localcircle\\/chat:/ {print $1; exit}')
if [ -z "$cid" ]; then
  echo LC_BUDDY NOCHAT
  exit 0
fi
docker exec -i "$cid" python - <<'PY'
REMOTE_PYTHON
PY
"""


_REMOTE_PYTHON = r"""
import uuid

from sqlalchemy import select

from app.db.models import Conversation, DailyBuddyPair, DailyBuddySlot, Profile, User
from app.db.session import SessionLocal
from app.services.daily_buddy import _create, buddy_day

LEFT = "PHONE_LEFT"
RIGHT = "PHONE_RIGHT"


def say(message):
    print("LC_BUDDY " + message, flush=True)


def account(db, phone):
    digits = phone[-10:]
    rows = list(db.scalars(select(User).where(User.phone.in_([phone, digits, "91" + digits]))))
    live = [row for row in rows if row.deleted_at is None]
    exact = [row for row in live if row.phone == phone]
    return (exact or live or [None])[0]


def slot_for(db, user, day):
    slot = db.scalar(
        select(DailyBuddySlot).where(DailyBuddySlot.user_id == user.id, DailyBuddySlot.buddy_date == day)
    )
    if slot is None:
        return None, None, False
    pair = db.get(DailyBuddyPair, slot.pair_id)
    conversation = None if pair is None else db.get(Conversation, pair.conversation_id)
    active = (
        pair is not None
        and pair.removed_at is None
        and conversation is not None
        and conversation.type == "daily_buddy"
    )
    return slot, pair, active


def other_phone(db, pair, user):
    other_id = pair.user_high if pair.user_low == user.id else pair.user_low
    other = db.get(User, other_id)
    return other.phone if other is not None and other.phone else str(other_id)


db = SessionLocal()
try:
    day = buddy_day()
    left = account(db, LEFT)
    right = account(db, RIGHT)
    if left is None:
        say("MISSING " + LEFT)
    elif right is None:
        say("MISSING " + RIGHT)
    elif left.id == right.id:
        say("ERROR Enter two different numbers.")
    else:
        left_slot, left_pair, left_open = slot_for(db, left, day)
        right_slot, right_pair, right_open = slot_for(db, right, day)
        same = left_open and right_open and left_pair is not None and right_pair is not None and left_pair.id == right_pair.id
        if same:
            say("ALREADY " + day.isoformat())
        elif left_open:
            say("BUSY " + LEFT + " " + other_phone(db, left_pair, left))
        elif right_open:
            say("BUSY " + RIGHT + " " + other_phone(db, right_pair, right))
        else:
            low, high = (left.id, right.id) if str(left.id) < str(right.id) else (right.id, left.id)
            existing = db.scalar(
                select(DailyBuddyPair).where(
                    DailyBuddyPair.buddy_date == day,
                    DailyBuddyPair.user_low == low,
                    DailyBuddyPair.user_high == high,
                )
            )
            conversation = None if existing is None else db.get(Conversation, existing.conversation_id)
            if conversation is not None and conversation.type != "daily_buddy":
                say("ERROR These two already kept this chat, so they cannot be paired again today.")
            elif existing is not None:
                existing.removed_at = None
                existing.removed_by = None
                for person in (left, right):
                    slot = db.scalar(
                        select(DailyBuddySlot).where(
                            DailyBuddySlot.user_id == person.id,
                            DailyBuddySlot.buddy_date == day,
                        )
                    )
                    if slot is None:
                        db.add(
                            DailyBuddySlot(
                                id=uuid.uuid4(),
                                user_id=person.id,
                                buddy_date=day,
                                pair_id=existing.id,
                            )
                        )
                    else:
                        slot.pair_id = existing.id
                    profile = db.get(Profile, person.id)
                    if profile is None:
                        continue
                    profile.daily_buddy_enabled = True
                    if profile.daily_buddy_from is not None and profile.daily_buddy_from > day:
                        profile.daily_buddy_from = None
                db.commit()
                say("PAIRED " + day.isoformat() + " 1 " + str(existing.conversation_id))
            else:
                freed = 0
                if left_slot is not None:
                    db.delete(left_slot)
                    freed += 1
                if right_slot is not None and (left_slot is None or right_slot.id != left_slot.id):
                    db.delete(right_slot)
                    freed += 1
                for person in (left, right):
                    profile = db.get(Profile, person.id)
                    if profile is None:
                        continue
                    profile.daily_buddy_enabled = True
                    if profile.daily_buddy_from is not None and profile.daily_buddy_from > day:
                        profile.daily_buddy_from = None
                db.flush()
                pair = _create(db, left.id, right.id, day, detail={"source": "handler"})
                say("PAIRED " + day.isoformat() + " " + ("1" if freed else "0") + " " + str(pair.conversation_id))
except Exception as exc:
    db.rollback()
    say("ERROR " + exc.__class__.__name__ + ": " + str(exc).splitlines()[0][:240])
finally:
    db.close()
"""
