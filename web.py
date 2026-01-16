from importlib import import_module
from celery import Celery, Task
from flask import Flask
from lib import config
from lib.aes_token import AESCipher, ConfidentialTokenService

cfg = config.Config.from_yaml('conf.yaml')


def create_app() -> Flask:
    app = Flask(__name__)

    class Config:
        TASK_LOG_BACKEND = cfg.redis.task_log_backend
        SECRET_KEY = cfg.secret_key
        CELERY = dict(
            broker_url=cfg.redis.broker_url,
            result_backend=cfg.redis.result_backend
        )
        IPC_KEY = cfg.IPC_key
        GLOBAL_CFG = cfg

    app.config.from_object(Config)
    _celery_init_app(app)
    _aesgcm_init_app(app)
    _register_blueprints(app)
    return app


def _register_blueprints(app):
    """Import each blueprints.<name> package and register its 'bp' object."""
    for provider in cfg.monitor:
        for tool in provider.tools:
            mod = import_module(f"providers.monitor.{provider.name}.tools.{tool}")

            bp_cfg = provider.raw[tool]
            # Expect each package to expose create_blueprint(cfg)
            bp = mod.create_blueprint(bp_cfg)

            app.register_blueprint(bp)

    # also register core functionality BPs
    core = import_module("providers.monitor.Base.tools.Core")
    stats = import_module("providers.monitor.Base.tools.Statistics")

    core_bp = core.create_blueprint()
    stats_bp = stats.create_blueprint()

    app.register_blueprint(core_bp)
    app.register_blueprint(stats_bp)


def _celery_init_app(app: Flask) -> Celery:
    class FlaskTask(Task):
        def __call__(self, *args: object, **kwargs: object) -> object:
            with app.app_context():
                return self.run(*args, **kwargs)

    celery_app = Celery(app.name, task_cls=FlaskTask)
    celery_app.config_from_object(app.config["CELERY"])
    celery_app.set_default()
    app.extensions["celery"] = celery_app
    return celery_app


def _aesgcm_init_app(app: Flask) -> None:
    aes_key = AESCipher.derive_key_from_secret(app.config["SECRET_KEY"])

    app.extensions["AESCipher"] = AESCipher(aes_key)
    app.extensions["AESTokenService"] = ConfidentialTokenService()
    app.extensions["AESTokenService"].add_key(0, aes_key)
