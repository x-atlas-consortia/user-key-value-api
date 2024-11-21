import logging
import threading
import json
import re
from contextlib import closing
from app_db import DBConn

import mysql.connector.errors
import werkzeug
from flask import Response, Request, make_response

# HuBMAP commons
from hubmap_commons.hm_auth import AuthHelper
from hubmap_commons.S3_worker import S3Worker

# Set up scalars with SQL strings matching the paramstyle of the database module, as
# specified at https://peps.python.org/pep-0249
#
# Using the "format" paramstyle with mysql-connector-python module for MySQL 8.0+
#
# Ignore threat of unsanitized user input for SQL injection, XSS, etc. due to current
# nature of site at AWS, modification requests come from Globus authenticated users,
# microservice rejection of non-valid JSON, microservice use of prepared statements,
# MySQL rejection of non-valid JSON as KEY_VALUE, etc.
SQL_UPSERT_USERID_KEY_VALUE = \
    ("INSERT INTO user_key_value"
     " (GLOBUS_IDENTITY_ID, KEY_NAME, KEY_VALUE, UPSERT_UTC_TIME)"
     " VALUES"
     " (%s, %s, %s, NOW())"
     " ON DUPLICATE KEY UPDATE"
     " GLOBUS_IDENTITY_ID=%s"
     " ,KEY_NAME=%s"
     " ,KEY_VALUE=%s"
     " ,UPSERT_UTC_TIME=NOW()"
     )

SQL_SELECT_USERID_KEY_VALUE = \
    ("SELECT GLOBUS_IDENTITY_ID AS globus_id"
     "       ,KEY_NAME AS keyname"
     "       ,KEY_VALUE AS keyvalue"
     " FROM user_key_value"
     " WHERE GLOBUS_IDENTITY_ID = %s"
     "   AND KEY_NAME = %s"
     )

SQL_SELECT_USERID_ALL = \
    ("SELECT GLOBUS_IDENTITY_ID AS globus_id"
     "       ,KEY_NAME AS key"
     "       ,KEY_VALUE AS value"
     " FROM user_key_value"
     " WHERE GLOBUS_IDENTITY_ID = %s"
     )

SQL_DELETE_USERID_KEY_VALUE = \
    ("DELETE FROM user_key_value"
     " WHERE GLOBUS_IDENTITY_ID = %s"
     "   AND KEY_NAME = %s"
     )

class UserKeyValueWorker:
    authHelper = None

    def __init__(self, app_config=None):
        self.logger = logging.getLogger('user-key-value.service')

        if app_config is None:
            raise Exception("Configuration data loaded by the app must be passed to the worker.")
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
                raise Exception(f"Unexpected error: {str(e)}")

        except KeyError as ke:
            self.logger.error("Expected configuration failed to load %s from app_config=%s.",ke,app_config)
            raise Exception("Expected configuration failed to load. See the logs.")

        ####################################################################################################
        ## AuthHelper initialization
        ####################################################################################################
        if not clientId  or not clientSecret:
            raise Exception("Globus client id and secret are required in AuthHelper")
        # Initialize AuthHelper class and ensure singleton
        try:
            if not AuthHelper.isInitialized():
                self.authHelper = AuthHelper.create(    clientId,
                                                        clientSecret)
                self.logger.info('Initialized AuthHelper class successfully')
            else:
                self.authHelper = AuthHelper.instance()
        except Exception:
            msg = 'Failed to initialize the AuthHelper class'
            # Log the full stack trace, prepend a line with our message
            self.logger.exception(msg)

        ####################################################################################################
        ## MySQL database connection
        ####################################################################################################
        self.dbHost = dbHost
        self.dbName = dbName
        self.dbUsername = dbUsername
        self.dbPassword = dbPassword
        self.lock = threading.RLock()
        self.hmdb = DBConn(self.dbHost, self.dbUsername, self.dbPassword, self.dbName)

    def validateKey(self, a_key:str):
        if len(a_key) > 50:
            self.logger.error(f"Length {len(a_key)} is longer than database-supported keys for"
                              f" key={a_key}.")
            return make_response(f"Specified key '{a_key}' is longer than supported. See logs."
                                 , 400)
        if re.match(pattern='.*\s.*', string=a_key, ):
            self.logger.error(f"Whitespace is not allowed in database-supported keys for"
                              f" key='{a_key}'.")
            return make_response(f"Specified key '{a_key}' contains whitespace. See logs."
                                 , 400)
        # Return nothing if the key is valid
        return

    def _get_globus_id_for_request(self, req:Request):
        # Get user information dict based on the http request(headers)
        # `group_required` is a boolean, when True, 'hmgroupids' is in the output
        # user_info is a dict
        user_info = self.authHelper.getUserInfoUsingRequest(httpReq=req)
        self.logger.info("======user_info======")
        self.logger.info(user_info)
        if isinstance(user_info, Response):
            # Return of a Response object indicates an error retrieving user information
            return user_info
        if 'sub' not in user_info:
            self.logger.error(f"Unable to find 'sub' entry in user_info={str(user_info)}")
            return make_response(   f"Unable to retrieve Globus Identity ID for user.  See logs."
                                    , 400)
        return user_info['sub']

    '''
    req - the GET request for single key
    valid_key - the key for which the associated value should be retrieved
    '''
    def getKeyValue(self, req:Request, valid_key:str(50)):

        globus_id = self._get_globus_id_for_request(req)
        if isinstance(globus_id, Response):
            # Return of a Response object indicates an error accessing the user's Globus Identity ID
            return globus_id

        with (closing(self.hmdb.getDBConnection()) as dbConn):
            with closing(dbConn.cursor(prepared=True)) as curs:
                try:
                    # execute() parameter substitution queries with a data tuple.
                    curs.execute(SQL_SELECT_USERID_KEY_VALUE,
                                 (globus_id, valid_key))
                    res = curs.fetchone()
                    if res is None:
                        return make_response(f"Unable to find key '{valid_key}' for user '{globus_id}'.", 404)
                    # If the result tuple size matches the number of columns expected from
                    # SQL_SELECT_USERID_KEY_VALUE, assume result is correct. Return the
                    # "value" column as JSON.
                    if len(res) == 3:
                        return make_response(res[2]
                                             , 200
                                             , {'Content-Type': 'application/json'})
                    else:
                        self.logger.error(f"Unexpected result from SQL_SELECT_USERID_KEY_VALUE query. Returned"
                                          f" res='{str(res)}' rather than tuple of expected length for"
                                          f" globus_id={globus_id}, valid_key={valid_key}.")
                        return make_response(f"Unexpected error retrieving key '{valid_key}'. See logs."
                                             , 500)
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
                            f" Reached end of getKeyValue() retrieving key '{valid_key}'"
                            f" for globus_id='{globus_id}'")
        return make_response(f"Unexpected execution flow retrieving key '{valid_key}'. See logs."
                             , 500)

    def upsertKeyValue(self, req:Request, valid_key:str(50)):

        globus_id = self._get_globus_id_for_request(req)
        if isinstance(globus_id, Response):
            # Return of a Response object indicates an error accessing the user's Globus Identity ID
            return globus_id

        if not req.is_json:
            return make_response(   f"Invalid input, JSON value to store for key '{valid_key}' is missing."
                                    , 400)
        # Verify the value to go into the database is valid JSON
        try:
            user_key_value = json.dumps(req.get_json())
        except werkzeug.exceptions.BadRequest as br:
            self.logger.error(msg=f"JSON decoding caused caused br='{br}'"
                                  f" for globus_id='{globus_id}',"
                                  f" valid_key='{valid_key}',"
                                  f" with req.data='{str(req.data)}'")
            return make_response(   f"Invalid input, value to store for key '{valid_key}'"
                                    f" cannot be decoded as valid JSON."
                                    , 400)
        if user_key_value is None or len(user_key_value) <= 0:
            return make_response(   f"Invalid input, JSON value to store for key '{valid_key}' is empty."
                                    , 400)

        with (closing(self.hmdb.getDBConnection()) as dbConn):
            existing_autocommit_setting = dbConn.autocommit
            dbConn.autocommit = False
            try:
                with closing(dbConn.cursor(prepared=True)) as curs:
                    # Count on DBAPI-compliant MySQL Connector/Python to begin a transaction on the first
                    # SQL statement and keep open until explicit commit() call to allow rollback(), so
                    # all table modifications committed atomically.

                    # Because our query uses a MySQL "upsert" structure to support both INSERT/PUT/create and
                    # UPDATE/POST/update operations, and because the current MySQL driver we use does not
                    # support named parameters, repeat the arguments in the tuple to align with the
                    # positional %s markers in the prepared statement.
                    curs.execute(SQL_UPSERT_USERID_KEY_VALUE
                                 , (globus_id, valid_key, user_key_value,
                                    globus_id, valid_key, user_key_value))
                dbConn.commit()
            except mysql.connector.errors.Error as dbErr:
                dbConn.rollback()
                self.logger.error(  msg=f"upsertKeyValue() database failure caused rollback: '{dbErr}'"
                                        f" for globus_id='{globus_id}',"
                                        f" valid_key='{valid_key}',"
                                        f" user_key_value='{user_key_value}'")
                raise dbErr

            # restore the autocommit setting, even though closing it by going out of scope.
            dbConn.autocommit = existing_autocommit_setting
            return make_response()

    def testConnection(self):
        try:
            res = None
            with closing(self.hmdb.getDBConnection()) as dbConn:
                with closing(dbConn.cursor(prepared=True)) as curs:
                    curs.execute("select 'ANYTHING'")
                    res = curs.fetchone()

            if (res is None or len(res) == 0): return False
            if (res[0] == 'ANYTHING'):
                return True
            else:
                return False
        except Exception as e:
            self.logger.error(e, exc_info=True)
            return False
