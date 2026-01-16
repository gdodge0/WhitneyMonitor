import hashlib
import redis
import time
import json
from cryptography.exceptions import InvalidTag
from flask import Blueprint, render_template, stream_with_context, Response, request, current_app, make_response, \
    jsonify
from contextlib import suppress
from lib.aes_token import TokenValidationError
from . import tasks  # ensure Celery tasks imported
from . import utils


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
        "title": "Inyo ATC",
        "description": "This tool interfaces with Recreation.gov to streamline Inyo National Forest permit "
                       "reservations.",
        "image": "https://upload.wikimedia.org/wikipedia/commons/f/f9/Mount_Whitney_2003-03-25.jpg"
    }

    # init redis
    r = redis.Redis.from_url(cfg["redis_url"])

    # -- Make the config reachable everywhere -----------------
    bp.cfg = cfg  # 1. Attr on the Blueprint instance

    # pub sub channel format
    CHANNEL_FMT = utils.CHANNEL_FMT

    # helpers

    def _event_stream(txid: str):
        channel = CHANNEL_FMT.format(txid=txid)
        pubsub = r.pubsub()
        pubsub.subscribe(channel)

        PING_INTERVAL = 25
        POLL_TIMEOUT = 1
        last_ping = time.time()

        try:
            while True:
                msg = pubsub.get_message(ignore_subscribe_messages=True,
                                         timeout=POLL_TIMEOUT)

                if msg:
                    data = msg["data"]
                    # ⬇Convert bytes → str if necessary
                    if isinstance(data, (bytes, bytearray)):
                        data = data.decode("utf-8", errors="replace")

                    yield f"data: {data}\n\n"
                    # Stop once the producer marks it done
                    with suppress(json.JSONDecodeError):
                        if json.loads(data).get("done"):
                            break

                # keep-alive ping
                now = time.time()
                if now - last_ping >= PING_INTERVAL:
                    last_ping = now
                    yield ": ping\n\n"
        finally:
            pubsub.unsubscribe(channel)
            pubsub.close()

    def generate_task_key(task_name, args, kwargs):
        kwargs = kwargs or {}
        key_data = {
            'task': task_name,
            'args': args,
            'kwargs': kwargs
        }
        key_str = json.dumps(key_data, sort_keys=True)
        return f"task:{task_name}:{hashlib.md5(key_str.encode()).hexdigest()}"

    def check_task_exists(task, args=None, kwargs=None, ttl=300):
        args = args or []
        kwargs = kwargs or {}
        key = generate_task_key(task, args, kwargs)

        # Use SET with NX and EX to set if not exists and expire automatically
        was_set = r.set(key, "queued", nx=True, ex=ttl)

        if was_set:
            return False  # Task doesn't exist
        else:
            return True  # Task exists and should fail

    def get_spoof_headers():
        # get selected browser headers
        SELECTED_HEADERS = ["User-Agent", "Accept", "Accept-Language", "Accept-Encoding",
                            "Connection", "Sec-Fetch-Dest", "Sec-Fetch-Mode", "Sec-Fetch-Site",
                            "DNT"]

        headers = {key: request.headers[key] for key in SELECTED_HEADERS if key in request.headers}
        return headers

    @bp.record_once  # 2. Merged into app.config
    def on_load(state):
        state.app.config.setdefault(f"{cfg['name']}_CFG", {}).update(cfg)

    @bp.after_request
    def add_sse_headers(resp):
        """Attach SSE‑friendly headers when route sets Content‑Type."""
        if resp.headers.get("Content-Type") == "text/event-stream":
            resp.headers["Cache-Control"] = "no-cache"
            resp.headers["Connection"] = "keep-alive"
            resp.headers["X-Accel-Buffering"] = "no"  # disable nginx buffering if applicable
        return resp

    # -- Views ------------------------------------------------
    @bp.route("/")
    def index():
        return render_template("Inyo/index.html")

    @bp.route("/atc")
    def atc():
        return render_template("Inyo/atc.html")

    @bp.route("/api/stream/<tx_id>")
    def sse(tx_id):
        return Response(stream_with_context(_event_stream(tx_id)), mimetype="text/event-stream")

    @bp.route("/api/atc", methods=["POST"])
    def api_atc():
        try:
            atc_data = current_app.extensions["AESTokenService"].verify_token(request.json.get("token", None))
        except TokenValidationError as e:
            return Response(str(e), status=400)

        # guaranteed to exist, provided & signed by monitor thread
        date = atc_data["date"]
        permit = atc_data["permit"]
        target = atc_data["target"]
        count = atc_data["count"]

        fingerprint = request.json.get("fingerprint", None)

        if not isinstance(fingerprint, str):
            return Response("missing required field(s)", status=400)

        headers = get_spoof_headers()

        username = request.cookies.get("rec_username")
        encrypted_password = request.cookies.get("rec_password")

        if encrypted_password:
            try:
                password = current_app.extensions["AESCipher"].decrypt_b64(encrypted_password).decode('utf-8')
            except InvalidTag:
                password = None  # PW cannot be determined
        else:
            password = None

        if not username or not password:
            if cfg["backup_credentials"]["username"] and cfg["backup_credentials"]["password"]:
                username = cfg["backup_credentials"]["username"]
                password = cfg["backup_credentials"]["password"]
            else:
                return Response("No username or password provided", status=400)

        if check_task_exists("tasks.inyo.atc", args=[date, count, permit, target, username], ttl=60):
            return Response("Duplicate Task", status=400)
            # duplicate task

        try:
            task = current_app.extensions["celery"].send_task("tasks.inyo.atc",
                                                              args=[date, count, permit, target, username,
                                                                    password, fingerprint, headers,
                                                                    current_app.config["GLOBAL_CFG"].to_dict()])
            return jsonify({"txId": task.id})
        except Exception as e:
            return Response(str(e), status=400)

    @bp.route('/api/validate', methods=['POST'])
    def api_validate():
        username = request.json.get("username", None)
        password = request.json.get("password", None)
        fingerprint = request.json.get("fingerprint", None)
        headers = get_spoof_headers()

        if not isinstance(fingerprint, str) or (not isinstance(password, str)) or not isinstance(username, str):
            return Response("missing required field(s)", status=400)

        if cfg["features"]["enable_account_validation"]:
            task = current_app.extensions["celery"].send_task("tasks.inyo.validate", args=[username, password,
                                                                                           fingerprint, headers,
                                                                                           current_app.config[
                                                                                               "GLOBAL_CFG"]
                                                              .to_dict()])

            result = task.get(timeout=10)

        else:
            result = True  # skip if feat not enabled

        if result:
            response = make_response(jsonify({'success': True}))
            response.set_cookie(
                key="rec_password",
                value=current_app.extensions["AESCipher"].encrypt_b64(password),
                httponly=True,
                max_age=31536000,
                secure=True,
                samesite='Strict'
            )
            response.set_cookie(
                key="rec_username",
                value=username,
                httponly=False,
                max_age=31536000,
                secure=True,
                samesite='Strict'
            )
            return response

        return jsonify({'success': False, 'error': 'could not verify account'}), 400

    return bp
