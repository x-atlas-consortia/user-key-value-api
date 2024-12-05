import os
from pathlib import Path
import logging
from typing import Annotated

import requests
# Don't confuse urllib (Python native library) with urllib3 (3rd-party library, requests also uses urllib3)
from requests.packages.urllib3.exceptions import InsecureRequestWarning

# from ukv_exceptions import UKVDataStoreQueryException, UKVWorkerException, UKVKeyFormatException, \
#     UKVKeyNotFoundException, UKVValueFormatException, UKVRequestFormatException
from ukv_worker import UserKeyValueWorker
import ukv_exceptions as ukvEx

from flask import Flask, request, jsonify, make_response, Request

# HuBMAP commons
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

Parameters
----------
None

Returns
-------
HTTP 200 Response with Content-Type application/json
The JSON body is a dictionary with the status details.
"""
# Status of MySQL connection
@app.route('/status', methods=['GET'])
def status():
    global ukv_worker

    # Use strip() to remove leading and trailing spaces, newlines, and tabs
    status_data = {
        'version': (Path(__file__).absolute().parent.parent / 'VERSION').read_text().strip(),
        'build': (Path(__file__).absolute().parent.parent / 'BUILD').read_text().strip(),
        'mysql_connection': ukv_worker.test_connection()
    }
    return jsonify(status_data)

"""
An endpoint to create or update a key/value pair for the authenticated user.
The value is valid JSON attached to the HTTP Request.

Parameters
----------
key : The key to store the value under for the authenticated user, as specified on the API endpoint. The
      key cannot contain any whitespace, the characters %, _, *, #,',",`, or the character sequences /*, */, or --.

Returns
-------
HTTP 200 Response with Content-Type application/json
The JSON body is a dictionary with an entry confirming the key/value pair was stored.

HTTP 400 Response with Content-Type application/json
The JSON body is a dictionary with an error message describing the problem that kept
the key/value pair from being stored.  Examples causes are
- A body in the Request which is not valid JSON to be stored as a key's value
- Failure to specify a Content-Type header of application/json for the Request
- A body in the Request which is valid JSON but is empty
- Keys which are too long
- Keys which contain whitespace or other disallowed characters

HTTP 500 Response
An unexpected error in the server, including unexpected problems with the data store.
"""
@app.route(rule='/user/keys/<key>', methods=["PUT"])
@secured(has_write=True)
def upsert_key_value(key: Annotated[str, 50]):
    global ukv_worker

    # Make sure the key is valid before passing it on to a database query
    try:
        ukv_worker.validate_key(a_key=key)
    except ukvEx.UKVKeyFormatException as e_400:
        return jsonify({'error': e_400.message}), 400

    try:
        success_msg = ukv_worker.upsert_key_value(req=request
                                                  , valid_key=key)
        return jsonify({'message': success_msg})
    except (ukvEx.UKVValueFormatException, ukvEx.UKVRequestFormatException) as e_400:
        return jsonify({'error': e_400.message}), 400
    except (ukvEx.UKVDataStoreQueryException, Exception) as e_500:
        msg = f"Unexpected error setting key '{key}'."
        logger.exception(e_500)
        return jsonify({'error': f"{msg} See logs."}), 500

"""
An endpoint to retrieve the value matching the given key for the authenticated user.

Parameters
----------
key : The name of the user's key for which to retrieve the value. 

Returns
-------
HTTP 200 Response with Content-Type application/json
The JSON stored as the value for the given key.

HTTP 400 Response with Content-Type application/json
The JSON body is a dictionary with an error message describing the problem that kept
key/value pair from being retrieved.  Examples causes are
- Keys which are too long
- Keys which contain whitespace or other disallowed characters

HTTP 404 Response with Content-Type application/json
The JSON body is a dictionary with an error message indicating the key was not found in the data store.

HTTP 500 Response
An unexpected error in the server, including unexpected problems with the data store.
"""
@app.route(rule='/user/keys/<key>', methods=["GET"])
def get_key_value(key: Annotated[str, 50]):
    global ukv_worker

    # Make sure the key is valid before passing it on to a database query
    try:
        ukv_worker.validate_key(a_key=key)
    except ukvEx.UKVKeyFormatException as kfe:
        return jsonify({'error': kfe.message}), 400

    try:
        value_json = ukv_worker.get_key_value(req=request
                                              , valid_key=key)
        return make_response(value_json
                             , 200
                             , {'Content-Type': 'application/json'})
    except ukvEx.UKVKeyNotFoundException as e_404:
        return jsonify({'error': e_404.message}), 404
    except (ukvEx.UKVDataStoreQueryException, ukvEx.UKVWorkerException, Exception) as e_500:
        msg = f"Unexpected error retrieving key '{key}'."
        logger.exception(e_500)
        return jsonify({'error': f"{msg} See logs."}), 500

"""
An endpoint to retrieve the key/value pairs for the authenticated user which match the
keys in the list of the Request JSON payload.

Parameters
----------
None

Returns
-------
HTTP 200 Response with Content-Type application/json
The JSON body is a list containing a dictionary for each key/value
pair for the user matching the request list.
Each key/value dictionary will have a "key" element which
is a string for a valid UTF-8 key name, and a "value" element which is valid JSON.

HTTP 400 Response with Content-Type application/json
The JSON body is a dictionary with an error message describing the problem that kept
key/value pairs from being retrieved.  Examples causes are
- A body in the Request which is not valid JSON to be processed as a list of key names
- Failure to specify a Content-Type header of application/json for the Request
- A body in the Request which is valid JSON but is empty
- Keys which are too long
- Keys which contain whitespace or other disallowed characters


HTTP 404 Response with Content-Type application/json
The JSON body is a dictionary with an error message indicating the keys not found in the data store. Keys
absent from the error message can be assumed to be in the data store, but are only returned on the
JSON payload of an HTTP 200 Response.

HTTP 500 Response
An unexpected error in the server, including unexpected problems with the data store.
"""
@app.route(rule='/user/find/keys', methods=["POST"])
def find_named_key_values():
    global ukv_worker

    try:
        user_key_values_dict = ukv_worker.find_named_key_values(req=request)
        # The user_key_values_dict successfully retrieved from the data store is
        # already JSON, so make a Response to attach it to.
        return make_response(user_key_values_dict
                             , 200
                             , {'Content-Type': 'application/json'})
    except (ukvEx.UKVRequestFormatException, ukvEx.UKVValueFormatException, ukvEx.UKVKeyFormatException) as e_400:
        return jsonify({'error': e_400.message}), 400
    except (ukvEx.UKVBadKeyListException) as e_400:
        return jsonify(e_400.data), 400
    except (ukvEx.UKVKeyNotFoundException) as e_404:
        return jsonify({'error': e_404.message}), 404
    except (ukvEx.UKVRequestedKeysNotFoundException) as e_404:
        return jsonify(e_404.data), 404
    except (Exception) as e_500:
        msg = f"Unexpected error retrieving all key/value data for user."
        logger.exception(e_500)
        return jsonify({'error': f"{msg} See logs."}), 500

"""
An endpoint to retrieve all the key/value pairs for the authenticated user.

Parameters
----------
None

Returns
-------
HTTP 200 Response with Content-Type application/json
A JSON list containing a dictionary for each key/value pair the user has.
Each key/value dictionary will have a "key" element which
is a string for a valid UTF-8 key name, and a "value" element which is valid JSON.

HTTP 404 Response with Content-Type application/json
The JSON body is a dictionary with an error message indicating no keys for the user
were found in the data store.

HTTP 500 Response
An unexpected error in the server, including unexpected problems with the data store.
"""
@app.route(rule='/user/keys', methods=["GET"])
def get_all_key_values():
    global ukv_worker

    try:
        user_key_values_dict = ukv_worker.get_all_key_values(req=request)
        return make_response(user_key_values_dict
                             , 200
                             , {'Content-Type': 'application/json'})
    except ukvEx.UKVKeyNotFoundException as e_404:
        return jsonify({'error': e_404.message}), 404
    except (ukvEx.UKVDataStoreQueryException, ukvEx.UKVWorkerException, Exception) as e_500:
        msg = f"Unexpected error retrieving all key/value data for user."
        logger.exception(e_500)
        return jsonify({'error': f"{msg} See logs."}), 500

"""
An endpoint to create or update a collection of key/value pairs for the authenticated user which
are specified in the JSON payload of the HTTP Request.  The JSON body is a list containing a
dictionary for each key/value pair for the user to store.  Each key must be valid, and each value must
be valid JSON itself. No key/value pair is stored unless all key/value pairs are stored, as indicated
by an HTTP 200 Response.

Parameters
----------
None

Returns
-------
HTTP 200 Response with Content-Type application/json
The JSON body is a dictionary with an entry confirming all the key/value pairs were stored.

HTTP 400 Response with Content-Type application/json
The JSON body is a dictionary with an error message describing the problem that kept
each key/value pair from being stored.  Examples causes are
- A body in the Request which is not valid JSON for a dictionary of key/value pairs
- Failure to specify a Content-Type header of application/json for the Request
- A body in the Request which is valid JSON but is empty
- Keys which are too long
- Keys which contain whitespace or other disallowed characters
Key/value pairs in the Request which are absent from error message can be assumed to
pass validations, but were not stored if the HTTP Response is not 200.

HTTP 500 Response
An unexpected error in the server, including unexpected problems with the data store.
"""
@app.route(rule='/user/keys', methods=["PUT"])
@secured(has_write=True)
def upsert_key_values():
    global ukv_worker

    try:
        success_msg = ukv_worker.upsert_key_values(req=request)
        return jsonify({'message': success_msg})
    except (ukvEx.UKVValueFormatException, ukvEx.UKVRequestFormatException) as e_400:
        return jsonify({'error': e_400.message}), 400
    except (ukvEx.UKVBadKeyListException) as e_400:
        return jsonify(e_400.data), 400
    except (ukvEx.UKVDataStoreQueryException, Exception) as e_500:
        logger.exception(f"Unexpected error setting key/value pair(s) in JSON payload={request.get_json()}")
        return jsonify({'error': f"Unexpected error setting key/value pair(s). See logs."}), 500

"""
An endpoint to delete a key/value pair for the authenticated user.
On success returns an HTTP 200 Response.

Parameters
----------
key : The key delete for the authenticated user, as specified on the API endpoint 

Returns
-------
HTTP 200 Response with Content-Type application/json
The JSON body is a dictionary with an entry confirming the key/value pair was deleted.

HTTP 400 Response with Content-Type application/json
The JSON body is a dictionary with an error message describing the problem that kept
each key/value pair from being deleted.  Examples causes are
- Keys which are too long
- Keys which contain whitespace or other disallowed characters

HTTP 404 Response with Content-Type application/json
The JSON body is a dictionary with an error message indicating the key was not found in the data store.

HTTP 500 Response
An unexpected error in the server, including unexpected problems with the data store.
"""
@app.route(rule='/user/keys/<key>', methods=["DELETE"])
@secured(has_write=True)
def delete_key_value(key: Annotated[str, 50]):
    global ukv_worker

    # Make sure the key is valid before passing it on to a database query
    try:
        ukv_worker.validate_key(a_key=key)
    except ukvEx.UKVKeyFormatException as e_400:
        return jsonify({'error': e_400.message}), 400

    try:
        success_msg = ukv_worker.delete_key_value(req=request
                                                  , valid_key=key)
        return jsonify({'message': success_msg})
    except (ukvEx.UKVValueFormatException, ukvEx.UKVRequestFormatException) as e_400:
        return jsonify({'error': e_400.message}), 400
    except ukvEx.UKVKeyNotFoundException as e_404:
        return jsonify({'error': e_404.message}), 404
    except (ukvEx.UKVDataStoreQueryException, Exception) as e_500:
        msg = f"Unexpected error deleting key '{key}'."
        logger.exception(e_500)
        return jsonify({'error': f"{msg} See logs."}), 500

if __name__ == "__main__":
    try:
        app.run(host='0.0.0.0', port="5006")
    except Exception as e:
        print("Error during starting debug server.")
        print(str(e))
        logger.error(e, exc_info=True)

