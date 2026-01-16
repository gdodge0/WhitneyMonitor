from functools import wraps
from flask import request, current_app, jsonify


def require_ipc_key(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Try to get the IPC key from the header or request args/form
        ipc_key = request.headers.get('X-IPC-KEY') or \
                  request.args.get('ipc_key')

        expected_key = current_app.config.get('IPC_KEY')

        if not expected_key:
            return jsonify({'error': 'Server configuration missing IPC_KEY'}), 500

        if ipc_key != expected_key:
            return jsonify({'error': 'Invalid IPC key'}), 403

        return f(*args, **kwargs)

    return decorated_function
