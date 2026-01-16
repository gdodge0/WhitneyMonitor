from celery import shared_task
from helpers.app_init import flask_init_app
from browser.main import Browser
from helpers.exceptions import SigninError

flask_app = flask_init_app()
celery = flask_app.extensions["celery"]


@shared_task(ignore_result=False)
def atc(date, count, permit_id, username, password):
    browser = Browser()
    browser.init_rec(username, password)
    browser.reserve_date(date, int(count), permit_id)
    return True


@shared_task(ignore_result=False)
def validate(username, password):
    browser = Browser()
    try:
        browser.init_rec(username, password)
        return True
    except SigninError:
        return False
