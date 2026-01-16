import datetime
from celery import shared_task
from .atc import InyoAtc
from .exceptions import SigninError

OVERNIGHT_LOOKUP = ["JM35", "166"]  # whitney overnight is special for inyo NF


def get_next_day(date_str: str) -> str:
    """
    Takes a date string in 'YYYY-MM-DD' format and returns the next day's date
    in the same format. Handles leap years and month/year rollovers.

    Args:
        date_str (str): The input date string.

    Returns:
        str: The next day's date string.

    Raises:
        ValueError: If the input is not a valid date in the correct format.
    """
    try:
        date_obj = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        next_day = date_obj + datetime.timedelta(days=1)
        return next_day.isoformat()
    except ValueError as e:
        raise ValueError(f"Invalid date format or value: {e}")


@shared_task(
    name="tasks.inyo.atc",
    bind=True,
    max_retries=1,
    default_retry_delay=0,
    ignore_result=True
)
def atc(self, date, count, permit_id, target_id, username, password, fingerprint, headers, cfg):
    task_id = self.request.id

    if permit_id in OVERNIGHT_LOOKUP:
        start_date = date
        end_date = get_next_day(date)
    else:
        start_date = date
        end_date = date

    inyo = InyoAtc(username, password, fingerprint, headers, cfg, prep_v3=True, txid=task_id)
    inyo.login()
    inyo.add_to_cart(permit_id, target_id, start_date, end_date, count)


@shared_task(
    name="tasks.inyo.validate",
    max_retries=0,
)
def validate(username, password, fingerprint, headers, cfg):
    inyo = InyoAtc(username, password, fingerprint, headers, cfg)
    try:
        inyo.login()
        return True
    except SigninError:
        return False
