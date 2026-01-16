from flask import Flask
from celery import Celery, Task
from helpers import env
from helpers.aes_token import *


def celery_init_app(app: Flask) -> Celery:
    class FlaskTask(Task):
        def __call__(self, *args: object, **kwargs: object) -> object:
            with app.app_context():
                return self.run(*args, **kwargs)

    celery_app = Celery(app.name, task_cls=FlaskTask)
    celery_app.config_from_object(app.config["CELERY"])
    celery_app.set_default()
    app.extensions["celery"] = celery_app
    return celery_app


def aesgcm_init_app(app: Flask) -> None:
    aes_key = AESCipher.derive_key_from_secret(app.config["SECRET_KEY"], length=32)

    app.extensions["AESCipher"] = AESCipher(aes_key)
    app.extensions["AESTokenService"] = ConfidentialTokenService()
    app.extensions["AESTokenService"].add_key(0, aes_key)


def flask_init_app() -> Flask:
    app = Flask(__name__, template_folder="../templates")

    class Config:
        TASK_LOG_BACKEND = env.get_string("TASK_LOG_BACKEND")
        SECRET_KEY = env.get_string("SECRET_KEY")
        CELERY = dict(
            broker_url=env.get_string("CELERY_BROKER_URL"),
            result_backend=env.get_string("CELERY_RESULT_BACKEND"),
            task_ignore_result=True,
        ),
        if env.get_boolean("ENABLE_UNSAFE"):
            ENABLE_ACCOUNT_VALIDATION = env.get_boolean("ENABLE_ACCOUNT_VALIDATION")
            USE_BACKUP_CREDENTIALS = env.get_boolean("USE_BACKUP_CREDENTIALS")
            BACKUP_USERNAME = env.get_string("BACKUP_USERNAME")
            BACKUP_PASSWORD = env.get_string("BACKUP_PASSWORD")
            SHOW_BACKUP_CREDENTIALS = env.get_boolean("SHOW_BACKUP_CREDENTIALS")
        else:
            ENABLE_ACCOUNT_VALIDATION = False
            USE_BACKUP_CREDENTIALS = False
            BACKUP_USERNAME = None
            BACKUP_PASSWORD = None
            SHOW_BACKUP_CREDENTIALS = False

    app.config.from_object(Config)
    celery_init_app(app)
    aesgcm_init_app(app)
    return app
