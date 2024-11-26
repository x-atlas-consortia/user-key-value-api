import os
from pathlib import Path
import logging
import requests
# Don't confuse urllib (Python native library) with urllib3 (3rd-party library, requests also uses urllib3)
from requests.packages.urllib3.exceptions import InsecureRequestWarning

from src.ukv_exceptions import UKVValueFormatException
from ukv_exceptions import UKVDataStoreQueryException, UKVWorkerException, UKVKeyFormatException, \
    UKVKeyNotFoundException, UKVValueFormatException, UKVRequestFormatException
from ukv_worker import UserKeyValueWorker
import json
from flask import Flask, request, Response, make_response, jsonify

# HuBMAP commons
# from hm_auth import secured
from hubmap_commons.hm_auth import secured

# Root logger configuration
global logger

# Set logging format and level (default is warning)
# All the API logging is forwarded to the uWSGI server and gets written into the log file `log/uwsgi-ukv-api.log`
# Log rotation is handled via logrotate on the host system with a configuration file
# Do NOT handle log file and rotation via the Python logging to avoid issues with multi-ukv_worker processes
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

ukv_worker = None
try:
    ukv_worker = UserKeyValueWorker(app_config=app.config)
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

"""
The default route

Returns
-------
str
    A welcome message
"""
@app.route('/', methods=['GET'])
def index():
    return "Hello! This is the User Key/Value API service :)"

"""
Show the current VERSION and BUILD, as well as the status of MySQL connection.

Returns
-------
json
    A json containing the status details
"""
# Status of MySQL connection
@app.route('/status', methods=['GET'])
def status():
    global ukv_worker

    status_data = {
        # Use strip() to remove leading and trailing spaces, newlines, and tabs
        'version': (Path(__file__).absolute().parent.parent / 'VERSION').read_text().strip(),
        'build': (Path(__file__).absolute().parent.parent / 'BUILD').read_text().strip(),
        'mysql_connection': False
    }

    dbcheck = ukv_worker.testConnection()
    if dbcheck:
        status_data['mysql_connection'] = True

    return jsonify(status_data)

"""
An endpoint to create or update a key/value pair for the authenticated user.
The value is valid JSON attached to the HTTP Request.
On success returns an HTTP 200 Response.

Parameters
----------
key : The key to store the value under for the authenticated user, as specified on the API endpoint 

Returns
-------
HTTP 200 Response
"""
@app.route(rule='/user/keys/<key>', methods=["PUT"])
@secured(has_write=True)
def upsert_key_value(key):
    global ukv_worker

    # Make sure the key is valid before passing it on to a database query
    try:
        ukv_worker.validateKey(a_key=key)
    except UKVKeyFormatException as e_400:
        resp = jsonify({'error': e_400.message})
        resp.status = 400
        return resp

    try:
        success_msg = ukv_worker.upsertKeyValue(req=request
                                                , valid_key=key)
        return jsonify({'message': success_msg})
    except (UKVValueFormatException, UKVRequestFormatException) as e_400:
        resp = jsonify({'error': e_400.message})
        resp.status = 400
        return resp
    except (UKVDataStoreQueryException, Exception) as e_500:
        msg = f"Unexpected error setting key '{key}'."
        logger.exception(e_500)
        resp = jsonify({'error': f"{msg} See logs."})
        resp.status = 500
        return resp

"""
An endpoint to retrieve the value matching the given key for the authenticated user.
On success returns the JSON value for the user's key

Parameters
----------
key : The user's key to be retrieved, as specified on the API endpoint 

Returns
-------
Valid JSON stored under the user's key
"""
@app.route(rule='/user/keys/<key>', methods=["GET"])
def get_key_value(key):
    global ukv_worker

    # Make sure the key is valid before passing it on to a database query
    try:
        ukv_worker.validateKey(a_key=key)
    except UKVKeyFormatException as kfe:
        resp = jsonify({'error': kfe.message})
        resp.status = 400
        return resp

    try:
        value_json = ukv_worker.getKeyValue(    req=request
                                                , valid_key=key)
        return make_response(value_json
                             , 200
                             , {'Content-Type': 'application/json'})
    except UKVKeyNotFoundException as e_404:
        resp = jsonify({'error': e_404.message})
        resp.status = 404
        return resp
    except (UKVDataStoreQueryException, UKVWorkerException, Exception) as e_500:
        msg = f"Unexpected error retrieving key '{key}'."
        logger.exception(e_500)
        resp = jsonify({'error': f"{msg} See logs."})
        resp.status = 500
        return resp

if __name__ == "__main__":
    try:
        app.run(host='0.0.0.0', port="5006")
    except Exception as e:
        print("Error during starting debug server.")
        print(str(e))
        logger.error(e, exc_info=True)
        print("Check the log file for further information: " + LOG_FILE_NAME)
