import copy
import logging
import threading
import json
import re
from contextlib import closing
from typing import Annotated

from app_db import DBConn

import mysql.connector.errors
import werkzeug
from flask import Request, Response

# HuBMAP commons
from hubmap_commons.hm_auth import AuthHelper
from hubmap_commons.S3_worker import S3Worker
from hubmap_commons.string_helper import listToCommaSeparated

# from ukv_exceptions import UKVConfigurationException, UKVKeyFormatException, UKVKeyNotFoundException, \
#     UKVDataStoreQueryException, UKVWorkerException, UKVValueFormatException, UKVRequestFormatException
import ukv_exceptions as ukvEx
import ukv_prepared_statments as ukvPS

class UserKeyValueWorker:
    authHelper = None

    def __init__(self, app_config=None):
        self.logger = logging.getLogger('user-key-value.service')

        if app_config is None:
            raise ukvEx.UKVConfigurationException("Configuration data loaded by the app must be passed to the worker.")
        try:
            ####################################################################################################
            ## Load configuration variables used by this class
            ####################################################################################################
            clientId = app_config['APP_CLIENT_ID']
            clientSecret = app_config['APP_CLIENT_SECRET']
            dbHost = app_config['DB_HOST']
            dbName = app_config['DB_NAME']
            dbUsername = app_config['DB_USERNAME']
            dbPassword = app_config['DB_PASSWORD']

            ####################################################################################################
            ## S3Worker initialization
            ####################################################################################################
            if 'LARGE_RESPONSE_THRESHOLD' not in app_config \
                or not isinstance(app_config['LARGE_RESPONSE_THRESHOLD'], int) \
                or app_config['LARGE_RESPONSE_THRESHOLD'] > 10*(2**20)-1:
                self.logger.error(f"There is a problem with the LARGE_RESPONSE_THRESHOLD setting in app.cfg."
                                  f" Defaulting to small value so noticed quickly.")
                large_response_threshold = 5000000
            else:
                large_response_threshold = int(app_config['LARGE_RESPONSE_THRESHOLD'])

            self.logger.info(f"large_response_threshold set to {large_response_threshold}.")
            self.S3_settings_dict = {   'large_response_threshold': large_response_threshold
                                        ,'aws_access_key_id': app_config['AWS_ACCESS_KEY_ID']
                                        ,'aws_secret_access_key': app_config['AWS_SECRET_ACCESS_KEY']
                                        ,'aws_s3_bucket_name': app_config['AWS_S3_BUCKET_NAME']
                                        ,'aws_object_url_expiration_in_secs': app_config['AWS_OBJECT_URL_EXPIRATION_IN_SECS']
                                        ,'service_configured_obj_prefix': app_config['AWS_S3_OBJECT_PREFIX']}
            try:
                self.theS3Worker = S3Worker(ACCESS_KEY_ID=self.S3_settings_dict['aws_access_key_id']
                                            , SECRET_ACCESS_KEY=self.S3_settings_dict['aws_secret_access_key']
                                            , S3_BUCKET_NAME=self.S3_settings_dict['aws_s3_bucket_name']
                                            , S3_OBJECT_URL_EXPIRATION_IN_SECS=self.S3_settings_dict['aws_object_url_expiration_in_secs']
                                            , LARGE_RESPONSE_THRESHOLD=self.S3_settings_dict['large_response_threshold']
                                            , SERVICE_S3_OBJ_PREFIX=self.S3_settings_dict['service_configured_obj_prefix'])
                self.logger.info("self.theS3Worker initialized")
            except Exception as e:
                self.logger.error(f"Error initializing self.theS3Worker - '{str(e)}'.", exc_info=True)
                raise ukvEx.UKVConfigurationException(f"Unexpected error: {str(e)}")

        except KeyError as ke:
            self.logger.error("Expected configuration failed to load %s from app_config=%s.",ke,app_config)
            raise ukvEx.UKVConfigurationException("Expected configuration failed to load. See the logs.")

        ####################################################################################################
        ## AuthHelper initialization
        ####################################################################################################
        if not clientId  or not clientSecret:
            raise ukvEx.UKVConfigurationException("Globus client id and secret are required in AuthHelper")
        # Initialize AuthHelper class and ensure singleton
        try:
            if not AuthHelper.isInitialized():
                self.authHelper = AuthHelper.create(    clientId,
                                                        clientSecret)
                self.logger.info('Initialized AuthHelper class successfully')
            else:
                self.authHelper = AuthHelper.instance()
        except Exception as e:
            msg = 'Failed to initialize the AuthHelper class'
            # Log the full stack trace, prepend a line with our message
            self.logger.exception(msg)
            raise ukvEx.UKVConfigurationException(msg)

        ####################################################################################################
        ## MySQL database connection
        ####################################################################################################
        self.dbHost = dbHost
        self.dbName = dbName
        self.dbUsername = dbUsername
        self.dbPassword = dbPassword
        self.lock = threading.RLock()
        self.dbUKV = DBConn(self.dbHost, self.dbUsername, self.dbPassword, self.dbName)

    # Check the validity of a single key. Return nothing if valid, or raise a known
    # exception for failed validations.
    def validate_key(self, a_key: Annotated[str, 50]):
        if len(a_key) > 50:
            self.logger.error(f"Length {len(a_key)} is longer than database-supported keys for"
                              f" key={a_key}.")
            raise ukvEx.UKVKeyFormatException(f"Specified key '{a_key}' is longer than supported.")
        if re.match(pattern='.*\s.*', string=a_key):
            self.logger.error(f"Whitespace is not allowed in database-supported keys for"
                              f" key='{a_key}'.")
            raise ukvEx.UKVKeyFormatException(f"Specified key '{a_key}' contains whitespace.")
        if re.match(pattern='.*[\'\"\`\#\_\%].*', string=a_key):
            self.logger.error(f"key='{a_key}' was rejected for containing an unsupported character.")
            raise ukvEx.UKVKeyFormatException(  f"The characters ',\",`,#,_, and % are not allowed in"
                                                f" database-supported keys for key='{a_key}'.")
        if re.match(pattern='.*(--|\*/|/\*).*', string=a_key):
            self.logger.error(f"key='{a_key}' was rejected for containing an unsupported character sequence.")
            raise ukvEx.UKVKeyFormatException(f"The character sequences */,/*, and -- are not allowed in"
                                              f" database-supported keys for key='{a_key}'.")
        # Return nothing if the key is valid
        return

    # Check the validity of each key in a list. Return nothing if all keys are valid. Raise a known
    # exception for any failed validation, with a dict attached to the exception describing each failed validation.
    def _validate_key_list(self, key_list:list):
        invalid_key_name_dict = {}
        for requested_key_name in key_list:
            try:
                self.validate_key(requested_key_name)
            except ukvEx.UKVKeyFormatException as ukfe:
                invalid_key_name_dict[requested_key_name] = ukfe.message

        if invalid_key_name_dict:
            error_msg_dict = {
                'error': f"Errors were found for {len(invalid_key_name_dict)} of the key strings submitted."
                , 'error_by_key': invalid_key_name_dict
            }
            raise ukvEx.UKVBadKeyListException( message=f"Invalid key format in request"
                                                , data=error_msg_dict)
        else:
            # Return nothing if all the keys are valid
            return

    # Extract the user information dict from the HTTP Request headers
    def _get_globus_id_for_request(self, req: Request):
        # user_info is a dict
        user_info = self.authHelper.getUserInfoUsingRequest(httpReq=req)
        self.logger.info("======user_info======")
        self.logger.info(user_info)
        if isinstance(user_info, Response):
            # Return of a Response object indicates an error retrieving user information
            return user_info
        if 'sub' not in user_info:
            self.logger.error(f"Unable to find 'sub' entry in user_info={str(user_info)}")
            raise ukvEx.UKVDataStoreQueryException(f"Unable to retrieve Globus Identity ID for user.")
        return user_info['sub']

    # Extract Python objects of the type required for the endpoint, and raise
    # exceptions when that cannot be done.
    def _load_endpoint_json(self, req:Request, endpoint_types:list) -> json:

        # Verify the Request has the correct header for the expected JSON payload for this endpoint
        if not req.is_json:
            raise ukvEx.UKVRequestFormatException("Invalid request. The HTTP Content-Type Header must indicate 'application/json'.")

        # Verify the value to go into the database is a valid, non-empty JSON array or object.
        try:
            json.dumps(req.get_json())
        except werkzeug.exceptions.BadRequest as br:
            raise ukvEx.UKVValueFormatException(f"Invalid input, payload cannot be decoded as valid JSON.")

        payload_json = req.get_json()
        if payload_json is None:
            raise ukvEx.UKVValueFormatException(f"Invalid input, JSON payload is empty.")
        if  not any(isinstance(payload_json, endpoint_type) for endpoint_type in endpoint_types):
            raise ukvEx.UKVValueFormatException(f"Invalid input, JSON value to store must load as one of: "
                                                f"{', '.join(endpoint_type.__name__ for endpoint_type in endpoint_types)}")
        if len(payload_json) <= 0:
                raise ukvEx.UKVValueFormatException(f"Invalid input, JSON payload is empty.")
        return payload_json

    '''
    req - the GET request for single key
    valid_key - the key for which the associated value should be retrieved
    '''
    def get_key_value(self, req: Request, valid_key: Annotated[str, 50]) -> str:

        globus_id = self._get_globus_id_for_request(req)
        if isinstance(globus_id, Response):
            # Return of a Response object indicates an error accessing the user's Globus Identity ID
            return globus_id

        with (closing(self.dbUKV.getDBConnection()) as dbConn):
            with closing(dbConn.cursor(prepared=True)) as curs:
                try:
                    # execute() parameter substitution queries with a data tuple.
                    curs.execute(ukvPS.SQL_SELECT_USERID_KEY_VALUE,
                                 (globus_id, valid_key))
                    res = curs.fetchone()
                    if res is None:
                        raise ukvEx.UKVKeyNotFoundException(f"Unable to find key '{valid_key}' for user '{globus_id}'.")

                    # If the result tuple size matches the number of columns expected from
                    # ukvPS.SQL_SELECT_USERID_KEY_VALUE, assume result is correct. Return the
                    # "value" column as JSON.
                    if len(res) == 3:
                        return res[2]
                    else:
                        self.logger.error(f"Unexpected result from ukvPS.SQL_SELECT_USERID_KEY_VALUE query. Returned"
                                          f" res='{str(res)}' rather than tuple of expected length for"
                                          f" globus_id={globus_id}, valid_key={valid_key}.")
                        raise ukvEx.UKVDataStoreQueryException(f"Unexpected error retrieving key '{valid_key}'.")

                    # Count on database referential integrity constraints to avoid more than one
                    # result for the globus_id+valid_key query, so don't use curs.fetchall() or
                    # check case for more results.
                except BaseException as err:
                    self.logger.error(  f"Unexpected database problem. err='{err}'"
                                        f" retrieving key '{valid_key}' for globus_id='{globus_id}'"
                                        f" Verify schema is current model.")
                    raise err
        # Expect preceding code to return or raise, and the following code to not be reached.
        self.logger.error(  f"Unexpected execution flow."
                            f" Reached end of get_key_value() retrieving key '{valid_key}'"
                            f" for globus_id='{globus_id}'")
        raise ukvEx.UKVWorkerException(f"Unexpected execution flow retrieving key '{valid_key}'.")

    '''
    Parameters
    ----------
    req - the POST request for certain named key/value pairs for the user

    Returns
    -------
    A Python List containing a Python Dictionary for each key/value
    pair the user has matching a key named in the Request.  Each key/value dictionary will have a
    "key" element which is a string for a valid UTF-8 key name, and a "value" element which is valid JSON.
    '''
    def find_named_key_values(self, req: Request) -> list:

        globus_id = self._get_globus_id_for_request(req)
        if isinstance(globus_id, Response):
            # Return of a Response object indicates an error accessing the user's Globus Identity ID
            return globus_id

        req_key_list = self._load_endpoint_json(req=req
                                                , endpoint_types=[list])

        self._validate_key_list(key_list=req_key_list) #req.get_json())

        with (closing(self.dbUKV.getDBConnection()) as dbConn):
            with closing(dbConn.cursor(prepared=True)) as curs:
                try:
                    # Generate a prepared statement with enough placeholders for each key name in
                    # the JSON payload to be placed in the MySQL IN clause.
                    prepared_stmt = ukvPS.SQL_SELECT_USERID_NAMED_KEY_VALUES_str.replace(   'generated_placeholders_for_named_keys'
                                                                                            , ', '.join(['%s'] * len(req.json)))
                    # execute() parameter substitution queries with a data tuple.
                    curs.execute(prepared_stmt,
                                 ([globus_id]+req_key_list))
                    res = curs.fetchall()

                    if res is None or len(res) != len(req_key_list):
                        unfound_key_list = [
                            req_key for req_key in req_key_list
                            if req_key.lower() not in (found_ukv[1].lower() for found_ukv in res)
                        ]

                        if unfound_key_list:
                            error_msg_dict = {
                                'error': f"Keys were not found for {len(unfound_key_list)} of the key strings submitted."
                                , 'unfound_keys': unfound_key_list
                            }
                            raise ukvEx.UKVRequestedKeysNotFoundException(  message=f"Invalid key format in request"
                                                                            , data=error_msg_dict)

                    # Iterate through each user key/value in the result set, form a Python Dictionary and add it to
                    # a List, which can be converted to JSON.
                    user_key_values_list = []
                    for ukv in res:
                        key_value_dict = {'key': ukv[1], 'value': json.loads(ukv[2])}
                        user_key_values_list.append(key_value_dict)
                    return user_key_values_list

                except BaseException as err:
                    self.logger.error(  f"Unexpected database problem. err='{err}'"
                                        f" finding named user key/value data for globus_id='{globus_id}'"
                                        f" Verify schema is current model.")
                    raise err
        # Expect preceding code to return or raise, and the following code to not be reached.
        self.logger.error(  f"Unexpected execution flow."
                            f" Reached end of find_named_key_values() finding named key/value data"
                            f" for globus_id='{globus_id}'")
        raise ukvEx.UKVWorkerException(f"Unexpected execution flow finding named key/value data for user.")

    '''
    Parameters
    ----------
    req - the GET request for all key/value pairs for the user

    Returns
    -------
    A Python List containing a Python Dictionary for each key/value
    pair the user has.  Each key/value dictionary will have a "key" element which
    is a string for a valid UTF-8 key name, and a "value" element which is valid JSON.
    '''
    def get_all_key_values(self, req: Request) -> list:

        globus_id = self._get_globus_id_for_request(req)
        if isinstance(globus_id, Response):
            # Return of a Response object indicates an error accessing the user's Globus Identity ID
            return globus_id

        with (closing(self.dbUKV.getDBConnection()) as dbConn):
            with closing(dbConn.cursor(prepared=True)) as curs:
                try:
                    # execute() parameter substitution queries with a data tuple.
                    curs.execute(ukvPS.SQL_SELECT_USERID_ALL,
                                 (globus_id,)) # N.B. comma needed to form single-value tuple for prepared statement.
                    res = curs.fetchall()
                    if not res:
                        raise ukvEx.UKVKeyNotFoundException(f"Unable to find any key/value data for user '{globus_id}'.")

                    # Iterate through each user key/value in the result set, form a Python Dictionary and add it to
                    # a List, which can be converted to JSON.
                    user_key_values_list = []
                    for ukv in res:
                        key_value_dict = {'key': ukv[1], 'value': json.loads(ukv[2])}
                        user_key_values_list.append(key_value_dict)
                    return user_key_values_list

                except BaseException as err:
                    self.logger.error(  f"Unexpected database problem. err='{err}'"
                                        f" retrieving all user key/value data for globus_id='{globus_id}'"
                                        f" Verify schema is current model.")
                    raise err
        # Expect preceding code to return or raise, and the following code to not be reached.
        self.logger.error(  f"Unexpected execution flow."
                            f" Reached end of get_all_key_values() retrieving all key/value data"
                            f" for globus_id='{globus_id}'")
        raise ukvEx.UKVWorkerException(f"Unexpected execution flow retrieving all key/value data for user.")

    def upsert_key_value(self, req: Request, valid_key: Annotated[str, 50]):

        globus_id = self._get_globus_id_for_request(req)
        if isinstance(globus_id, Response):
            # Return of a Response object indicates an error accessing the user's Globus Identity ID
            return globus_id

        user_key_value = self._load_endpoint_json(  req=req
                                                    , endpoint_types=[list, dict])

        with (closing(self.dbUKV.getDBConnection()) as dbConn):
            existing_autocommit_setting = dbConn.autocommit
            dbConn.autocommit = False
            try:
                with closing(dbConn.cursor(prepared=True)) as curs:
                    # Count on DBAPI-compliant MySQL Connector/Python to begin a transaction on the first
                    # SQL statement and keep open until explicit commit() call to allow rollback(), so
                    # all table modifications committed atomically.

                    curs.execute(ukvPS.SQL_UPSERT_USERID_KEY_VALUE
                                 , (globus_id, valid_key, json.dumps(req.get_json())))
                dbConn.commit()
            except mysql.connector.errors.Error as dbErr:
                dbConn.rollback()
                self.logger.error(  msg=f"upsert_key_value() database failure caused rollback: '{dbErr}'"
                                        f" for globus_id='{globus_id}',"
                                        f" valid_key='{valid_key}',"
                                        f" JSON value='{json.dumps(req.get_json())}'")
                raise ukvEx.UKVDataStoreQueryException(f"Failed to store value for key '{valid_key}'.")

            # restore the autocommit setting, even though closing it by going out of scope.
            dbConn.autocommit = existing_autocommit_setting
            return f"Value stored as '{valid_key}' for user '{globus_id}'."

    def upsert_key_values(self, req: Request):
        globus_id = self._get_globus_id_for_request(req)
        if isinstance(globus_id, Response):
            # Return of a Response object indicates an error accessing the user's Globus Identity ID
            return globus_id

        user_key_value_dict_list = self._load_endpoint_json(req=req
                                                            , endpoint_types=[list])

        # Flatten the dictionary of input aligned with the specification into a reasonable Python dictionary which
        # can be validated.  Also, put both the keys and values on a list which can then used for
        # parameter substitution in the prepared statement.
        new_user_key_value_dict = {}
        param_list = []
        for kv_dict in user_key_value_dict_list:
            if not isinstance(kv_dict, dict):
                raise ukvEx.UKVValueFormatException(f"Invalid input, only a list of dictionaries, each containing 'key' and 'value' entries, is accepted in the JSON payload.")
            if 'key' not in kv_dict or 'value' not in kv_dict:
                raise ukvEx.UKVValueFormatException(f"Invalid input, only a list of dictionaries, each containing 'key' and 'value' entries, is accepted in the JSON payload.")

            # Verify the value to go into the database is valid, non-empty JSON.
            try:
                value_json = json.dumps(kv_dict['value'])
            except werkzeug.exceptions.BadRequest as br:
                self.logger.error(msg=f"JSON decoding caused caused br='{br}'"
                                      f" for globus_id='{globus_id}',"
                                      f" key='{kv_dict['key']}',"
                                      f" value='{kv_dict['key']}'")
                raise ukvEx.UKVValueFormatException(f"Invalid input, value to store for key '{kv_dict['key']}'"
                                                    f" cannot be decoded as valid JSON.")
            if value_json is None or len(value_json) <= 0:
                raise ukvEx.UKVValueFormatException(f"Invalid input, JSON value to store for key '{kv_dict['key']}'"
                                                    f" is empty.")
            new_user_key_value_dict[kv_dict['key']] = value_json
            param_list.extend([kv_dict['key'], value_json])

        self._validate_key_list(key_list=list(new_user_key_value_dict.keys()))

        with (closing(self.dbUKV.getDBConnection()) as dbConn):
            stored_key_value_count = 0
            existing_autocommit_setting = dbConn.autocommit
            dbConn.autocommit = False
            try:
                with closing(dbConn.cursor(prepared=True)) as curs:

                    # Generate a prepared statement with enough placeholders for each key name in
                    # the JSON payload to be placed in the MySQL IN clause.
                    new_tuple_placeholder = f"('{globus_id}', %s, %s, NOW())"
                    prepared_stmt = ukvPS.SQL_UPSERT_USERID_KEY_VALUES_str.replace( 'generated_placeholders_for_new_tuples'
                                                                                    , ', '.join([new_tuple_placeholder] * len(user_key_value_dict_list)))

                    # execute() parameter substitution queries with a data tuple.
                    curs.execute(prepared_stmt,
                                 tuple(param_list))

                dbConn.commit()
                stored_key_value_count = len(user_key_value_dict_list)
            except mysql.connector.errors.Error as dbErr:
                dbConn.rollback()
                self.logger.error(  msg=f"upsert_key_values() database failure caused rollback: '{dbErr}'"
                                        f" for globus_id='{globus_id}',"
                                        f" JSON value='{json.dumps(req.get_json())}'")
                raise ukvEx.UKVDataStoreQueryException('Failed to store values for keys.')

            # restore the autocommit setting, even though closing it by going out of scope.
            dbConn.autocommit = existing_autocommit_setting
            return f"Stored {stored_key_value_count} key/value pairs for user."

    def delete_key_value(self, req: Request, valid_key: Annotated[str, 50]):

        globus_id = self._get_globus_id_for_request(req)
        if isinstance(globus_id, Response):
            # Return of a Response object indicates an error accessing the user's Globus Identity ID
            return globus_id

        rows_deleted = 0
        with (closing(self.dbUKV.getDBConnection()) as dbConn):
            existing_autocommit_setting = dbConn.autocommit
            dbConn.autocommit = False
            try:
                with closing(dbConn.cursor(buffered=True)) as curs:
                    # Count on DBAPI-compliant MySQL Connector/Python to begin a transaction on the first
                    # SQL statement and keep open until explicit commit() call to allow rollback(), so
                    # all table modifications committed atomically.

                    curs.execute(   ukvPS.SQL_DELETE_USERID_KEY_VALUE
                                    , (globus_id, valid_key))
                    rows_deleted = curs.rowcount
                dbConn.commit()
            except mysql.connector.errors.Error as dbErr:
                dbConn.rollback()
                self.logger.error(  msg=f"delete_key_value() database failure caused rollback: '{dbErr}'"
                                        f" for globus_id='{globus_id}',"
                                        f" valid_key='{valid_key}',")
                raise ukvEx.UKVDataStoreQueryException(f"Failed to delete key '{valid_key}'.")

            # restore the autocommit setting, even though closing it by going out of scope.
            dbConn.autocommit = existing_autocommit_setting
            if rows_deleted == 1:
                return f"Deleted value stored as '{valid_key}' for user '{globus_id}'."
            elif rows_deleted == 0:
                raise ukvEx.UKVKeyNotFoundException(f"Unable to find key '{valid_key}' for user '{globus_id}'.")
            else:
                self.logger.error("Deletion of key '{valid_key}' resulted in {rows_deleted} deletions instead of one row.")
                raise ukvEx.UKVDataStoreQueryException(f"Deletion of key '{valid_key}' resulted in {rows_deleted} deletions.")

    def test_connection(self):
        try:
            with closing(self.dbUKV.getDBConnection()) as dbConn:
                with closing(dbConn.cursor(prepared=True)) as curs:
                    curs.execute("select 'ANYTHING'")
                    res = curs.fetchone()

            if res is None or len(res) == 0: return False
            return res[0] == 'ANYTHING'
        except Exception as e:
            self.logger.error(e, exc_info=True)
            return False
