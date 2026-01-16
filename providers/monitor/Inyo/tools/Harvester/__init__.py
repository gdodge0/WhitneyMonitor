from flask import Blueprint, current_app, jsonify
from lib.flask_utils import require_ipc_key
from . import tasks  # ensure Celery tasks imported


def create_blueprint(cfg: dict) -> Blueprint:
    bp = Blueprint(
        cfg["name"],
        __name__,
        url_prefix=cfg["url_prefix"],
        template_folder="templates",
        static_folder="static",
        static_url_path="/static",
    )

    bp.meta = {
        "title": "ReCaptcha Harvester",
        "description": "An internal ReCaptcha API for WhitneyATC",
        "image": "https://upload.wikimedia.org/wikipedia/commons/a/ad/RecaptchaLogo.svg",
        "clickable": False
    }

    # -- Make the config reachable everywhere -----------------
    bp.cfg = cfg  # 1. Attr on the Blueprint instance

    @bp.record_once  # 2. Merged into app.config
    def on_load(state):
        state.app.config.setdefault(f"{cfg['name']}_CFG", {}).update(cfg)

    # -- Views ------------------------------------------------
    @bp.route("/start_token_task")
    @require_ipc_key
    def get_token():
        task = current_app.extensions["celery"].send_task("tasks.inyo.get_v3_token")
        return jsonify({'task_id': task.id}), 202

    @bp.route("/task_status/<task_id>", methods=['GET'])
    @require_ipc_key
    def task_status(task_id):
        task = tasks.get_v3_token.AsyncResult(task_id)
        if task.state == 'PENDING':
            response = {
                'state': task.state,
                'status': 'Pending...'
            }
        elif task.state != 'FAILURE':
            response = {
                'state': task.state,
                'result': task.result
            }
        else:
            # Something went wrong in the background job
            response = {
                'state': task.state,
                'status': str(task.info),  # Exception info
            }
        return jsonify(response)

    return bp
