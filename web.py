import traceback
import redis
import hashlib
import json
from tasks import flask_app
from flask import render_template, request, redirect, jsonify, make_response, url_for
from helpers.aes_token import TokenValidationError
from helpers.failure_msgs import failure_msgs
from browser.main import SigninError

app = flask_app
celery = flask_app.extensions["celery"]
AESCipher = flask_app.extensions["AESCipher"]
AESTokenService = flask_app.extensions["AESTokenService"]

r = redis.Redis.from_url(app.config['TASK_LOG_BACKEND'])


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


@app.route('/')
def index():
    return render_template("index.html")


@app.route('/atc/success')
def success():
    return render_template('success.html')


@app.route('/atc/fail')
def atc_fail():
    reason = request.args.get('r')
    if reason not in failure_msgs:
        reason = None  # generic failure

    return render_template("fail.html",
                           page_title=failure_msgs[reason]["page_title"],
                           section_title=failure_msgs[reason]["section_title"],
                           section_text=failure_msgs[reason]["section_text"])


@app.route('/atc')
def atc():
    token = request.args.get("token")

    try:
        atc_data = AESTokenService.verify_token(token)
    except TokenValidationError:
        return redirect(url_for('atc_fail', r="token"))

    date = atc_data["date"]
    permit = atc_data["permit"]
    count = atc_data["count"]

    username = request.cookies.get('rec_username')
    encrypted_password = request.cookies.get('rec_password')
    if encrypted_password:
        password = AESCipher.decrypt_b64(encrypted_password).decode('utf-8')
    else:
        password = None

    if not username or not password:
        if app.config['USE_BACKUP_CREDENTIALS']:
            username = app.config['BACKUP_USERNAME']
            password = app.config['BACKUP_PASSWORD']
        else:
            return redirect(url_for('atc_fail', r="missing_cred"))

    duplicate = check_task_exists("tasks.atc", args=[date, count, permit, username], ttl=300)

    if duplicate:
        return redirect(url_for('atc_fail', r="duplicate"))

    try:
        job = celery.send_task("tasks.atc", args=[date, count, permit, username, password])
        job.get(timeout=20)
        return redirect('/atc/success')
    except SigninError:
        reason = "invalid_cred"
    except Exception as e:
        reason = None  # generic ATC failure
        print(traceback.format_exception(e))

    # remove task from DB so that it doesn't block subsequent requests
    key = generate_task_key("tasks.atc", args=[date, count, permit, username], kwargs=None)
    r.delete(key)
    return redirect(url_for('atc_fail', r=reason))


@app.route('/api/validate', methods=['POST'])
def api_validate():
    username = request.json.get("username")
    password = request.json.get("password")

    if app.config['ENABLE_ACCOUNT_VALIDATION']:
        job = celery.send_task("tasks.validate", args=[username, password])
        result = job.get(timeout=20)
    else:
        result = True  # skip verification

    if result:
        response = make_response(jsonify({'success': True}))
        response.set_cookie(
            key="rec_password",
            value=AESCipher.encrypt_b64(password),
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


if __name__ == '__main__':
    app.run()
