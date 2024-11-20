import os
from pathlib import Path
import logging
import requests
# Don't confuse urllib (Python native library) with urllib3 (3rd-party library, requests also uses urllib3)
from requests.packages.urllib3.exceptions import InsecureRequestWarning

from src.user_key_value_worker import UserKeyValueWorker
from user_key_value_worker import UserKeyValueWorker
import json
from flask import Flask, request, Response, jsonify, make_response

# HuBMAP commons
# from hm_auth import secured
from hubmap_commons.hm_auth import secured
from hubmap_commons.string_helper import isBlank

# Root logger configuration
global logger

# Set logging format and level (default is warning)
# All the API logging is forwarded to the uWSGI server and gets written into the log file `log/uwsgi-uuid-api.log`
# Log rotation is handled via logrotate on the host system with a configuration file
# Do NOT handle log file and rotation via the Python logging to avoid issues with multi-worker processes
logging.basicConfig(    format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
                        , level=logging.DEBUG
                        , datefmt='%Y-%m-%d %H:%M:%S')

# Use `getLogger()` instead of `getLogger(__name__)` to apply the config to the root logger
# will be inherited by the sub-module loggers
try:
    logger = logging.getLogger()
    logger.info(f"Starting. Logger initialized, with logging level {logger.level}")
except Exception as e:
    print("Error opening log file during startup")
    print(str(e))

# Specify the absolute path of the instance folder and use the config file relative to the instance path
app = Flask(__name__, instance_path=os.path.join(os.path.abspath(os.path.dirname(__file__)), 'instance'),
            instance_relative_config=True)

# Use configuration from instance/app.cfg, deployed per-app from examples in the repository.
try:
    app.config.from_pyfile('app.cfg')
    logger.info("Application initialized from instance/app.cfg")
except Exception as e:
    logger.critical(f"Unable to initialize application from instance/app.cfg due to e='{str(e)}'")
    raise Exception("Failed to get configuration from instance/app.cfg")

worker = None
try:
    worker = UserKeyValueWorker(app_config=app.config)
    logger.info("UserKeyValueWorker instantiated using app.cfg setting.")
except Exception as e:
    logger.critical(f"Unable to instantiate a UserKeyValueWorker during startup.")
    print("Error instantiating a UserKeyValueWorker during startup.")
    print(str(e))
    logger.error(e, exc_info=True)
    print("Check the log file for further information.")

# Suppress InsecureRequestWarning warning when requesting status on https with ssl cert verify disabled
requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

####################################################################################################
## API Endpoints
####################################################################################################

@app.route('/', methods=['GET'])
def index():
    return "Hello! This is the User Key/Value API service :)"


# Status of MySQL connection
@app.route('/status', methods=['GET'])
def status():
    global worker

    response_data = {
        # Use strip() to remove leading and trailing spaces, newlines, and tabs
        'version': (Path(__file__).absolute().parent.parent / 'VERSION').read_text().strip(),
        'build': (Path(__file__).absolute().parent.parent / 'BUILD').read_text().strip(),
        'mysql_connection': False
    }

    dbcheck = worker.testConnection()
    if dbcheck:
        response_data['mysql_connection'] = True

    return jsonify(response_data)

@app.route(rule='/user/keys/<key>', methods=["POST"])
@app.route(rule='/user/keys/<key>', methods=["PUT"])
@secured(has_write=True)
def upsert_key_value(key):
    global worker

    # Make sure the key is valid before passing it on to upsert operations
    resp = worker.validateKey(a_key=key)
    if isinstance(resp, Response):
        return resp

    try:
        resp = worker.upsertKeyValue(req=request
                                     , valid_key=key)
        return resp
    except Exception as e:
        logger.error(e, exc_info=True)
        return make_response(f"Unexpected error setting key '{key}'. See logs."
                             , 500)

@app.route(rule='/user/keys/<key>', methods=["GET"])
def get_key_value(key):
    global worker

    # Make sure the key is valid before passing it on to upsert operations
    resp = worker.validateKey(a_key=key)
    if isinstance(resp, Response):
        return resp

    try:
        resp = worker.getKeyValue(req=request
                                  , valid_key=key)
        return resp
    except Exception as e:
        eMsg = str(e)
        logger.error(e, exc_info=True)
        return make_response('Unexpected error querying database.  See logs', 500)

@app.route('/uuid/<uuid>/exists', methods=["GET"])
@secured(has_read=True)
def is_uuid(uuid):
    global worker

    try:
        if request.method == "GET":
            exists = worker.getIdExists(uuid)
            if isinstance(exists, Response):
                return exists
            return json.dumps(exists)
        else:
            return Response("Invalid request use GET to check the status of a UUID", 500)
    except Exception as e:
        eMsg = str(e)
        logger.error(e, exc_info=True)
        return (Response("Unexpected error: " + eMsg, 500))


if __name__ == "__main__":
    try:
        app.run(host='0.0.0.0', port="5006")
    except Exception as e:
        print("Error during starting debug server.")
        print(str(e))
        logger.error(e, exc_info=True)
        print("Check the log file for further information: " + LOG_FILE_NAME)
